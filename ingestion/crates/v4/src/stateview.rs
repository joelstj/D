//! Uniswap V4 `StateView` reads — the seed and reconcile path for V4 pools
//! (`getSlot0(poolId)` / `getLiquidity(poolId)`), analogous to V3's
//! `slot0()`/`liquidity()`.

use crate::error::{Result, V4Error};
use alloy_primitives::{Address, Bytes, B256, U256};
use l2i_core::{Blockstamp, Pool, PoolKind, Token};
use l2i_ingest::mirror::{LiveState, Mirror, PoolState};
use l2i_ingest::reconcile::ReconcileTally;
use l2i_registry::gate::{ValidatedPool, ValidatedToken};
use l2i_registry::schema::{PoolEntry, DYNAMIC_FEE_FLAG};
use l2i_rpc::multicall::{require_all, Call3};
use l2i_rpc::{BlockId, ChainProvider};
use std::collections::BTreeMap;

const GET_SLOT0: [u8; 4] = [0xc8, 0x15, 0x64, 0x1c];
const GET_LIQUIDITY: [u8; 4] = [0xfa, 0x67, 0x93, 0xd5];

/// `StateView.getSlot0(poolId)` calldata.
pub fn get_slot0_calldata(pool_id: B256) -> Bytes {
    let mut v = GET_SLOT0.to_vec();
    v.extend_from_slice(pool_id.as_slice());
    v.into()
}

/// `StateView.getLiquidity(poolId)` calldata.
pub fn get_liquidity_calldata(pool_id: B256) -> Bytes {
    let mut v = GET_LIQUIDITY.to_vec();
    v.extend_from_slice(pool_id.as_slice());
    v.into()
}

/// Decode `getSlot0` → `(sqrtPriceX96, tick, protocolFee, lpFee)`.
pub fn decode_slot0(ret: &[u8]) -> Result<(U256, i32, u32, u32)> {
    if ret.len() < 128 {
        return Err(V4Error::Decode(format!(
            "getSlot0 return too short: {}",
            ret.len()
        )));
    }
    let sqrt_price_x96 = U256::from_be_slice(&ret[0..32]);
    let tick = i32::from_be_bytes([ret[60], ret[61], ret[62], ret[63]]);
    // protocolFee / lpFee are `uint24`, right-aligned in their word. Read the low 4
    // bytes directly instead of `U256::to::<u32>()`, which *panics* on a malformed
    // word with dirty high bits — a bad RPC return must yield a decode error/skip,
    // never crash the decode thread.
    let protocol_fee = u32::from_be_bytes([ret[92], ret[93], ret[94], ret[95]]);
    let lp_fee = u32::from_be_bytes([ret[124], ret[125], ret[126], ret[127]]);
    Ok((sqrt_price_x96, tick, protocol_fee, lp_fee))
}

/// Decode `getLiquidity` → the active liquidity.
pub fn decode_liquidity(ret: &[u8]) -> Result<U256> {
    if ret.len() < 32 {
        return Err(V4Error::Decode(format!(
            "getLiquidity return too short: {}",
            ret.len()
        )));
    }
    Ok(U256::from_be_slice(&ret[0..32]))
}

fn to_core_token(chain_id: u64, t: &ValidatedToken) -> Token {
    Token::with_symbol(chain_id, t.address, t.decimals, t.symbol.clone())
}

/// The effective fee for a V4 pool: the live `lp_fee` when the pool is dynamic-fee
/// (`PoolKey.fee == 0x800000`), else the declared static fee.
pub fn effective_fee(declared_fee: u32, live_lp_fee: u32) -> u32 {
    if declared_fee == DYNAMIC_FEE_FLAG {
        live_lp_fee
    } else {
        declared_fee
    }
}

/// Seed V4 pools via a batched `getSlot0`+`getLiquidity` `StateView` multicall at
/// `block` (two calls per pool), producing `verified` mirror entries. The V4 pool
/// is emitted with `kind:"v3"` and its `poolId` identity. Non-V4 entries are ignored.
pub async fn seed_v4_pools<P: ChainProvider + ?Sized>(
    provider: &P,
    pools: &[ValidatedPool],
    state_view: Address,
    blockstamp: Blockstamp,
    block: BlockId,
) -> Result<Vec<PoolState>> {
    let v4: Vec<&ValidatedPool> = pools
        .iter()
        .filter(|p| matches!(p.entry, PoolEntry::V4(_)))
        .collect();
    if v4.is_empty() {
        return Ok(vec![]);
    }

    let mut calls = Vec::with_capacity(v4.len() * 2);
    for p in &v4 {
        let id = p
            .entry
            .identity()
            .pool_id()
            .expect("a V4 entry's identity is a poolId");
        calls.push(Call3::required(state_view, get_slot0_calldata(id)));
        calls.push(Call3::required(state_view, get_liquidity_calldata(id)));
    }

    let results = provider.multicall(calls, block).await?;
    let blobs = require_all(results)?;
    let chain_id = provider.chain_id();

    let mut out = Vec::with_capacity(v4.len());
    for (i, p) in v4.iter().enumerate() {
        let (sqrt_price_x96, tick, _protocol_fee, lp_fee) = decode_slot0(&blobs[i * 2])?;
        let liquidity = decode_liquidity(&blobs[i * 2 + 1])?;
        out.push(PoolState {
            identity: p.entry.identity(),
            kind: PoolKind::V3, // V4 maps onto the engine's v3 shape
            fee_pips: effective_fee(p.fee_pips, lp_fee),
            token0: to_core_token(chain_id, &p.token0),
            token1: to_core_token(chain_id, &p.token1),
            state: LiveState::V3 {
                sqrt_price_x96,
                tick,
                liquidity,
            },
            blockstamp: blockstamp.clone(),
            verified: true,
        });
    }
    Ok(out)
}

/// Decode + compare a V4 pool's `StateView` `getSlot0`+`getLiquidity` blobs to its
/// mirror `v3` state. `None` on a decode failure (a non-fatal failed read, never a
/// mismatch). The effective fee of a dynamic pool legitimately moves per block and is
/// kept fresh on the live `Swap` path, so it is deliberately **not** compared here
/// (matching V3, which does not reconcile fee either).
fn v4_matches(pool: &Pool, slot0: &[u8], liquidity: &[u8]) -> Option<bool> {
    let v3 = pool.v3.as_ref()?;
    let (sqrt, tick, _protocol_fee, _lp_fee) = decode_slot0(slot0).ok()?;
    let lq = decode_liquidity(liquidity).ok()?;
    Some((sqrt, tick, lq) == (v3.sqrt_price_x96.0, v3.tick, v3.liquidity.0))
}

/// Reconcile V4 (`poolId`) pools against the chain via `StateView.getSlot0` +
/// `getLiquidity` at each pool's **own** blockstamp — the V4 analogue of
/// [`l2i_ingest::reconcile::reconcile_batch`], which skips `poolId` pools. A mismatch
/// flips the pool `verified:false` in `mirror` (drift / a missed `Swap` log / a silent
/// reorg → stop emitting it until the live path re-derives it), giving V4 pools the
/// **same verified-honesty guarantee as V2/V3** instead of being silently trusted.
///
/// Pools are grouped by block and each group's reads batched into one Multicall3.
/// Non-V4 (contract-address) pools in `pools` are ignored. Never returns an error: a
/// failed batch degrades to `failed` counts and leaves `verified` untouched, so a
/// flaky endpoint can't crash the ingestor.
pub async fn reconcile_v4_batch<P: ChainProvider + ?Sized>(
    provider: &P,
    mirror: &Mirror,
    state_view: Address,
    pools: &[Pool],
) -> ReconcileTally {
    let mut tally = ReconcileTally::default();

    // Group reconcilable V4 pools by their stamped block; ignore everything else.
    let mut by_block: BTreeMap<u64, Vec<usize>> = BTreeMap::new();
    for (i, p) in pools.iter().enumerate() {
        if p.address.pool_id().is_some() && p.v3.is_some() {
            by_block.entry(p.blockstamp.number).or_default().push(i);
        }
    }

    for (number, idxs) in by_block {
        let block = BlockId::from(number);
        // Two reads per pool (getSlot0, getLiquidity), batched into one aggregate3.
        let mut calls = Vec::with_capacity(idxs.len() * 2);
        for &i in &idxs {
            let id = pools[i]
                .address
                .pool_id()
                .expect("grouped pools are V4 poolId pools");
            calls.push(Call3::allow_failure(state_view, get_slot0_calldata(id)));
            calls.push(Call3::allow_failure(state_view, get_liquidity_calldata(id)));
        }

        let results = match provider.multicall(calls, block).await {
            Ok(r) => r,
            Err(e) => {
                tracing::debug!(error = %e, number, "V4 reconcile multicall failed");
                tally.failed += idxs.len() as u64;
                continue;
            }
        };

        let mut off = 0usize;
        for &i in &idxs {
            let p = &pools[i];
            let s0 = results.get(off);
            let lq = results.get(off + 1);
            off += 2;
            let verdict = match (s0, lq) {
                (Some(s0), Some(lq)) if s0.success && lq.success => {
                    v4_matches(p, &s0.returnData, &lq.returnData)
                }
                _ => None,
            };
            match verdict {
                Some(true) => tally.matched += 1,
                Some(false) => {
                    mirror.set_verified(&p.address, false);
                    tally.mismatched += 1;
                    tracing::warn!(
                        chain_id = provider.chain_id(),
                        pool = %p.address,
                        "V4 reconcile mismatch — pool marked verified:false"
                    );
                }
                None => tally.failed += 1,
            }
        }
    }

    tally
}

#[cfg(test)]
mod tests {
    use super::*;
    use l2i_core::{DecU256, PoolAddress, V3State};
    use l2i_rpc::mock::MockProvider;

    const CHAIN: u64 = 130; // Unichain

    fn state_view() -> Address {
        Address::from([0x5b; 20])
    }

    /// A `getSlot0` return blob: `sqrtPriceX96` in word 0, `tick` in the low 4 bytes of
    /// word 1; protocol/lp fee words left zero (not reconciled).
    fn slot0_blob(sqrt: U256, tick: i32) -> Vec<u8> {
        let mut b = vec![0u8; 128];
        b[0..32].copy_from_slice(&sqrt.to_be_bytes::<32>());
        b[60..64].copy_from_slice(&tick.to_be_bytes());
        b
    }

    fn liquidity_blob(liq: U256) -> Vec<u8> {
        liq.to_be_bytes::<32>().to_vec()
    }

    fn seed_v4(mirror: &Mirror, id: B256, sqrt: U256, tick: i32, liq: U256) {
        mirror.insert(PoolState {
            identity: PoolAddress::PoolId(id),
            kind: PoolKind::V3,
            fee_pips: 500,
            token0: Token::with_symbol(CHAIN, Address::from([1; 20]), 18, "WETH"),
            token1: Token::with_symbol(CHAIN, Address::from([2; 20]), 6, "USDC"),
            state: LiveState::V3 {
                sqrt_price_x96: sqrt,
                tick,
                liquidity: liq,
            },
            blockstamp: Blockstamp {
                chain_id: CHAIN,
                number: 100,
                block_hash: B256::from([100; 32]),
                timestamp: 100,
            },
            verified: true,
        });
    }

    #[tokio::test]
    async fn v4_reconcile_match_keeps_verified() {
        // The independent StateView read equals the mirror → the pool stays verified.
        let id = B256::from([9; 32]);
        let (sqrt, tick, liq) = (U256::from(123_456u64), 42, U256::from(999u64));
        let mirror = Mirror::new();
        seed_v4(&mirror, id, sqrt, tick, liq);

        let provider = MockProvider::new(CHAIN)
            .with_call(state_view(), get_slot0_calldata(id), slot0_blob(sqrt, tick))
            .with_call(
                state_view(),
                get_liquidity_calldata(id),
                liquidity_blob(liq),
            );

        let window = mirror.snapshot_verified();
        let tally = reconcile_v4_batch(&provider, &mirror, state_view(), &window).await;
        assert_eq!(tally.mismatched, 0);
        assert_eq!(tally.matched, 1);
        assert!(
            mirror.get(&PoolAddress::PoolId(id)).unwrap().verified,
            "a matching V4 pool stays verified:true"
        );
    }

    #[tokio::test]
    async fn v4_reconcile_mismatch_flips_unverified() {
        // The chain reports a different liquidity (a missed Swap / drift) → the pool must
        // be flipped verified:false, exactly as V2/V3 reconcile does. Without this wiring
        // it was silently counted as "matched" and left verified:true (the bug).
        let id = B256::from([9; 32]);
        let (sqrt, tick, liq) = (U256::from(123_456u64), 42, U256::from(999u64));
        let mirror = Mirror::new();
        seed_v4(&mirror, id, sqrt, tick, liq);

        let provider = MockProvider::new(CHAIN)
            .with_call(state_view(), get_slot0_calldata(id), slot0_blob(sqrt, tick))
            .with_call(
                state_view(),
                get_liquidity_calldata(id),
                liquidity_blob(U256::from(1u64)), // drifted
            );

        let window = mirror.snapshot_verified();
        let tally = reconcile_v4_batch(&provider, &mirror, state_view(), &window).await;
        assert_eq!(tally.mismatched, 1);
        assert!(
            !mirror.get(&PoolAddress::PoolId(id)).unwrap().verified,
            "a drifted V4 pool must be flipped verified:false"
        );
    }

    #[test]
    fn v4_matches_ignores_fee_words() {
        // v4_matches compares only sqrt/tick/liquidity, so different protocol/lp fee
        // words in the slot0 blob do not by themselves cause a mismatch (dynamic fee is
        // reconciled on the live Swap path, not here).
        let pool = Pool {
            address: PoolAddress::PoolId(B256::from([3; 32])),
            kind: PoolKind::V3,
            fee_pips: 500,
            verified: true,
            token0: Token::with_symbol(CHAIN, Address::from([1; 20]), 18, "WETH"),
            token1: Token::with_symbol(CHAIN, Address::from([2; 20]), 6, "USDC"),
            blockstamp: Blockstamp {
                chain_id: CHAIN,
                number: 1,
                block_hash: B256::ZERO,
                timestamp: 1,
            },
            v2: None,
            v3: Some(V3State {
                sqrt_price_x96: DecU256(U256::from(5u64)),
                tick: 7,
                liquidity: DecU256(U256::from(11u64)),
            }),
        };
        let mut s0 = slot0_blob(U256::from(5u64), 7);
        s0[124..128].copy_from_slice(&999u32.to_be_bytes()); // dirty lp-fee word
        assert_eq!(
            v4_matches(&pool, &s0, &liquidity_blob(U256::from(11u64))),
            Some(true),
            "fee words are not part of the reconcile comparison"
        );
    }
}
