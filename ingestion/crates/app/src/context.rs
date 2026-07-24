//! Per-chain [`ChainContext`] assembly: live gas + native-price derivation.
//!
//! Split out of the aggregator loop so it never runs on the per-tick hot path. A
//! dedicated per-chain refresher recomputes context off the block cadence and
//! publishes it via a watch channel (see `pipeline.rs`); the tick then reads a
//! cached value with zero RPCs. Gas price / L1 data fee move at most once per block,
//! so pulling them off the tick removes the only network round-trips that gated it.

use alloy_primitives::{Address, B256};
use l2i_amm::native::{
    build_native_price_map, native_price_in, v2_price_native_in_t, v3_price_native_in_t,
};
use l2i_config::ChainConfig;
use l2i_core::{ChainContext, PoolAddress};
use l2i_gas::GasConfig;
use l2i_ingest::mirror::{LiveState, Mirror};
use l2i_rpc::{BlockId, ChainProvider};
use std::collections::BTreeMap;
use std::str::FromStr;

/// Parse a pool-identity string: a 20-byte contract address or a 32-byte V4 poolId.
fn parse_pool_identity(s: &str) -> Option<PoolAddress> {
    let s = s.trim();
    let hex = s.strip_prefix("0x").unwrap_or(s);
    match hex.len() {
        40 => Address::from_str(s).ok().map(PoolAddress::Contract),
        64 => B256::from_str(s).ok().map(PoolAddress::PoolId),
        _ => None,
    }
}

/// Derive `native_price_in[T]` for each configured numeraire from the live WETH/T
/// pools already in the mirror (`docs/ENGINE_CONTRACT.md §7`). WETH is inferred as
/// the pool's *other* token — the side that isn't the numeraire key — so no separate
/// WETH config field is needed. A numeraire whose pricing pool isn't seeded yet, or
/// yields no finite positive price, is omitted: the engine then never gas-costs it,
/// which is the safe outcome (better than a wrong price).
pub fn derive_native_prices(cfg: &ChainConfig, mirror: &Mirror) -> BTreeMap<Address, f64> {
    let mut entries: Vec<(Address, Option<f64>)> = Vec::new();
    // The WETH we price everything against. If the config names it explicitly, we
    // *require* every pricing pool to contain it (else omit). Otherwise we adopt the
    // first pool's inferred WETH and require the rest to agree — so a stray pool that
    // doesn't actually pair with WETH (e.g. a USDC/USDT pool) is dropped, never
    // registered with a ~1800×-wrong price.
    let mut weth: Option<Address> = cfg
        .weth
        .as_deref()
        .and_then(|s| Address::from_str(s.trim()).ok());
    for (num_str, pool_str) in &cfg.native_price_pools {
        let Ok(num_addr) = Address::from_str(num_str.trim()) else {
            continue;
        };
        let Some(pool_id) = parse_pool_identity(pool_str) else {
            continue;
        };
        let Some(ps) = mirror.get(&pool_id) else {
            continue; // pool not seeded yet — priced once it is
        };
        // Identify the WETH side vs the numeraire side by matching the numeraire key.
        let (weth_is_token0, weth_dec, num_dec, weth_addr) = if ps.token0.address == num_addr {
            (
                false,
                ps.token1.decimals,
                ps.token0.decimals,
                ps.token1.address,
            )
        } else if ps.token1.address == num_addr {
            (
                true,
                ps.token0.decimals,
                ps.token1.decimals,
                ps.token0.address,
            )
        } else {
            continue; // numeraire is not a token in this pool → misconfigured entry
        };
        // Verify the inferred WETH side against the pinned/established WETH; omit on
        // disagreement rather than emitting a wrong price.
        match weth {
            Some(w) if w != weth_addr => {
                tracing::warn!(
                    pool = %pool_id, expected = %w, got = %weth_addr,
                    "native_price_pools entry does not pair the numeraire with WETH — omitting"
                );
                continue;
            }
            Some(_) => {}
            None => {
                // First pool (unpinned mode) establishes WETH.
                weth = Some(weth_addr);
            }
        }
        let human = match &ps.state {
            LiveState::V2 { reserve0, reserve1 } => {
                let (r_weth, r_t) = if weth_is_token0 {
                    (*reserve0, *reserve1)
                } else {
                    (*reserve1, *reserve0)
                };
                v2_price_native_in_t(r_weth, r_t, weth_dec, num_dec)
            }
            LiveState::V3 { sqrt_price_x96, .. } => {
                v3_price_native_in_t(*sqrt_price_x96, weth_is_token0, weth_dec, num_dec)
            }
        };
        entries.push((num_addr, human.map(|p| native_price_in(p, num_dec))));
    }
    match weth {
        Some(w) => build_native_price_map(w, entries),
        None => BTreeMap::new(), // no WETH/T pool seeded yet → empty (engine tolerates it)
    }
}

/// An initial [`ChainContext`] built from config alone (no RPC): gas params + hubs,
/// with a zero gas price and empty native-price map. It seeds the per-chain context
/// channel before the first live refresh; the aggregator only sends verified pools,
/// which don't exist until seeding completes and the ingestor has refreshed gas.
pub fn initial_context(cfg: &ChainConfig) -> ChainContext {
    let hubs: Vec<Address> = cfg.hubs.iter().filter_map(|s| s.parse().ok()).collect();
    let gas_cfg = GasConfig {
        base_gas: cfg.base_gas,
        per_hop_gas: cfg.per_hop_gas,
        gas_safety_multiplier: cfg.gas_safety_multiplier,
        min_profit_bps: cfg.min_profit_bps,
    };
    l2i_gas::assemble_chain_context(cfg.chain_id, 0, 0, gas_cfg, BTreeMap::new(), hubs)
}

/// Assemble a chain's [`ChainContext`]: live gas (execution price + L1 data fee) plus
/// the derived native-price map and hubs. Two RPCs (gas price + L1 fee) — run by the
/// refresher off the block cadence, never on the aggregator's per-tick path.
pub async fn build_chain_context<P: ChainProvider + ?Sized>(
    cfg: &ChainConfig,
    provider: &P,
    mirror: &Mirror,
) -> ChainContext {
    let gas_price = l2i_gas::read_gas_price(provider).await.unwrap_or(0);
    let model = l2i_chains::by_id(cfg.chain_id)
        .map(|s| s.gas_model)
        .unwrap_or(l2i_chains::GasModel::Arbitrum);
    let l1 = l2i_gas::read_l1_data_fee(
        provider,
        model,
        l2i_chains::OP_GAS_PRICE_ORACLE,
        alloy_primitives::Bytes::new(),
        BlockId::latest(),
    )
    .await
    .unwrap_or(0);
    let hubs: Vec<Address> = cfg.hubs.iter().filter_map(|s| s.parse().ok()).collect();
    let native = derive_native_prices(cfg, mirror);
    let gas_cfg = GasConfig {
        base_gas: cfg.base_gas,
        per_hop_gas: cfg.per_hop_gas,
        gas_safety_multiplier: cfg.gas_safety_multiplier,
        min_profit_bps: cfg.min_profit_bps,
    };
    l2i_gas::assemble_chain_context(cfg.chain_id, gas_price, l1, gas_cfg, native, hubs)
}

#[cfg(test)]
mod tests {
    use super::*;
    use alloy_primitives::U256;
    use l2i_core::{Blockstamp, PoolKind, Token};
    use l2i_ingest::mirror::PoolState;

    fn arbitrum_cfg() -> ChainConfig {
        l2i_config::Config::load(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../config/config.example.toml"
        ))
        .unwrap()
        .chains
        .into_iter()
        .find(|c| c.chain_id == 42161)
        .unwrap()
    }

    #[test]
    fn derives_real_native_price_for_usdc_from_v3_weth_usdc() {
        // Real Arbitrum WETH/USDC V3 slot0 — fixtures/arbitrum_amm.json, block
        // 484254173: token0=WETH(18), token1=USDC(6). The derived native_price_in
        // must reproduce the fixture's independently-computed value.
        let weth = Address::from_str("0x82aF49447D8a07e3bd95BD0d56f35241523fBab1").unwrap();
        let usdc = Address::from_str("0xaf88d065e77c8cC2239327C5EDb3A432268e5831").unwrap();
        let pool = Address::from_str("0xC6962004f452bE9203591991D15f6b388e09E8D0").unwrap();
        let sqrt = U256::from_str_radix("3471441003415396879153077", 10).unwrap();

        let mirror = Mirror::new();
        mirror.insert(PoolState {
            identity: PoolAddress::Contract(pool),
            kind: PoolKind::V3,
            fee_pips: 500,
            token0: Token::with_symbol(42161, weth, 18, "WETH"),
            token1: Token::with_symbol(42161, usdc, 6, "USDC"),
            state: LiveState::V3 {
                sqrt_price_x96: sqrt,
                tick: -200_721,
                liquidity: U256::from(1u64),
            },
            blockstamp: Blockstamp {
                chain_id: 42161,
                number: 484_254_173,
                block_hash: B256::ZERO,
                timestamp: 1,
            },
            verified: true,
        });

        let mut cfg = arbitrum_cfg();
        cfg.native_price_pools.clear();
        cfg.native_price_pools
            .insert(usdc.to_string(), pool.to_string());

        let map = derive_native_prices(&cfg, &mirror);
        assert_eq!(map.get(&weth), Some(&1.0), "WETH is 1.0 by definition");
        let npi = *map.get(&usdc).expect("USDC must be priced");
        let want = 1.919_819_551_793_729e-9;
        assert!(
            (npi - want).abs() / want < 1e-9,
            "native_price_in[USDC] {npi} vs expected {want}"
        );
    }

    #[test]
    fn pool_without_weth_is_omitted_when_weth_is_pinned() {
        // A USDC/USDT pool wrongly wired to price USDT. With `weth` pinned to the real
        // WETH, the entry must be OMITTED (its non-numeraire side is USDC, not WETH) —
        // never registered with a ~1800×-wrong price.
        let weth = Address::from_str("0x82aF49447D8a07e3bd95BD0d56f35241523fBab1").unwrap();
        let usdc = Address::from_str("0xaf88d065e77c8cC2239327C5EDb3A432268e5831").unwrap();
        let usdt = Address::from_str("0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9").unwrap();
        let pool = Address::from_str("0xC6962004f452bE9203591991D15f6b388e09E8D0").unwrap();

        let mirror = Mirror::new();
        mirror.insert(PoolState {
            identity: PoolAddress::Contract(pool),
            kind: PoolKind::V2,
            fee_pips: 100,
            token0: Token::with_symbol(42161, usdc, 6, "USDC"),
            token1: Token::with_symbol(42161, usdt, 6, "USDT"),
            state: LiveState::V2 {
                reserve0: U256::from(1_000_000u64),
                reserve1: U256::from(1_000_000u64),
            },
            blockstamp: Blockstamp {
                chain_id: 42161,
                number: 1,
                block_hash: B256::ZERO,
                timestamp: 1,
            },
            verified: true,
        });

        let mut cfg = arbitrum_cfg();
        cfg.weth = Some(weth.to_string()); // pin the real WETH
        cfg.native_price_pools.clear();
        cfg.native_price_pools
            .insert(usdt.to_string(), pool.to_string());

        let map = derive_native_prices(&cfg, &mirror);
        assert!(
            !map.contains_key(&usdt),
            "USDT must be omitted — its pool has no WETH side, not mispriced"
        );
    }

    #[test]
    fn unseeded_pool_is_omitted_not_faked() {
        // A numeraire whose pricing pool isn't in the mirror yet must be omitted —
        // never guessed. Only WETH (definitional 1.0) can appear, and only once a
        // pool reveals which token WETH is; with no seeded pool the map is empty.
        let cfg = arbitrum_cfg(); // native_price_pools point at unseeded placeholders
        let empty = Mirror::new();
        let map = derive_native_prices(&cfg, &empty);
        assert!(
            map.is_empty(),
            "no seeded WETH/T pool → empty native-price map, nothing fabricated"
        );
    }
}
