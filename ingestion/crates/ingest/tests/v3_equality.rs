//! M5 exit criterion — **event-derived `sqrt_price_x96`/`tick`/`liquidity` ==
//! `slot0()`/`liquidity()` at block N**, exactly, over recorded real Arbitrum data.
//!
//! `fixtures/arbitrum_v3_swap.json`: the block's last `Swap` for a real V3 pool
//! (ESP/USDC 0.01%), the `slot0()`+`liquidity()` reads at N, and a real
//! `aggregate3([slot0, liquidity])` seed response — all captured on-chain.

use alloy_primitives::{hex, Address, B256, U256};
use l2i_core::{Blockstamp, DetectRequest, Pool};
use l2i_ingest::event::decode_v3_swap_data;
use l2i_ingest::mirror::Mirror;
use l2i_ingest::v3::seed_v3_pools;
use l2i_ingest::{LiveState, PoolState};
use l2i_registry::gate::{ValidatedPool, ValidatedToken};
use l2i_registry::schema::{PoolEntry, V2V3Entry};
use l2i_rpc::mock::MockProvider;
use l2i_rpc::BlockId;
use serde_json::Value;

fn fixture() -> Value {
    let raw = std::fs::read_to_string(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/fixtures/arbitrum_v3_swap.json"
    ))
    .unwrap();
    serde_json::from_str(&raw).unwrap()
}
fn u256(s: &str) -> U256 {
    U256::from_str_radix(s, 10).unwrap()
}
fn addr(s: &str) -> Address {
    s.parse().unwrap()
}
fn bytes(s: &str) -> Vec<u8> {
    hex::decode(s).unwrap()
}
fn blockstamp(fx: &Value) -> Blockstamp {
    Blockstamp {
        chain_id: fx["chain_id"].as_u64().unwrap(),
        number: fx["block"].as_u64().unwrap(),
        block_hash: fx["block_hash"].as_str().unwrap().parse::<B256>().unwrap(),
        timestamp: fx["timestamp"].as_u64().unwrap(),
    }
}
fn validated(fx: &Value) -> ValidatedPool {
    let t0 = addr(fx["token0"].as_str().unwrap());
    let t1 = addr(fx["token1"].as_str().unwrap());
    ValidatedPool {
        entry: PoolEntry::V3(V2V3Entry {
            dex: "uniswap_v3".into(),
            address: addr(fx["pool"].as_str().unwrap()),
            fee_pips: fx["fee"].as_u64().unwrap() as u32,
            token0: t0,
            token1: t1,
            factory: None,
        }),
        token0: ValidatedToken {
            address: t0,
            decimals: fx["dec0"].as_u64().unwrap() as u8,
            symbol: fx["sym0"].as_str().unwrap().into(),
        },
        token1: ValidatedToken {
            address: t1,
            decimals: fx["dec1"].as_u64().unwrap() as u8,
            symbol: fx["sym1"].as_str().unwrap().into(),
        },
        fee_pips: fx["fee"].as_u64().unwrap() as u32,
    }
}

#[test]
fn event_derived_state_equals_slot0_liquidity_exactly() {
    let fx = fixture();
    let data = bytes(fx["swap_log"]["data"].as_str().unwrap());
    let (sqrt, liq, tick) = decode_v3_swap_data(&data).unwrap();
    let oc = &fx["onchain"];
    assert_eq!(
        sqrt,
        u256(oc["sqrt_price_x96"].as_str().unwrap()),
        "sqrtPriceX96"
    );
    assert_eq!(liq, u256(oc["liquidity"].as_str().unwrap()), "liquidity");
    assert_eq!(tick, oc["tick"].as_i64().unwrap() as i32, "tick");
}

#[tokio::test]
async fn multicall_seed_reproduces_slot0_liquidity() {
    let fx = fixture();
    let seed = &fx["seed_multicall"];
    let mock = MockProvider::new(42161).with_call(
        addr(seed["target"].as_str().unwrap()),
        bytes(seed["calldata"].as_str().unwrap()),
        bytes(seed["response"].as_str().unwrap()),
    );
    let seeded = seed_v3_pools(
        &mock,
        &[validated(&fx)],
        blockstamp(&fx),
        BlockId::from(fx["block"].as_u64().unwrap()),
    )
    .await
    .unwrap();
    assert_eq!(seeded.len(), 1);
    let oc = &fx["onchain"];
    match &seeded[0].state {
        LiveState::V3 {
            sqrt_price_x96,
            tick,
            liquidity,
        } => {
            assert_eq!(
                *sqrt_price_x96,
                u256(oc["sqrt_price_x96"].as_str().unwrap())
            );
            assert_eq!(*liquidity, u256(oc["liquidity"].as_str().unwrap()));
            assert_eq!(*tick, oc["tick"].as_i64().unwrap() as i32);
        }
        _ => panic!("expected V3 state"),
    }
}

#[test]
fn emitted_v3_pool_is_valid_contract_json() {
    let fx = fixture();
    let data = bytes(fx["swap_log"]["data"].as_str().unwrap());
    let (sqrt, liq, tick) = decode_v3_swap_data(&data).unwrap();
    let v = validated(&fx);
    let mirror = Mirror::new();
    mirror.insert(PoolState {
        identity: v.entry.identity(),
        kind: l2i_core::PoolKind::V3,
        fee_pips: v.fee_pips,
        token0: l2i_core::Token::with_symbol(
            42161,
            v.token0.address,
            v.token0.decimals,
            v.token0.symbol.clone(),
        ),
        token1: l2i_core::Token::with_symbol(
            42161,
            v.token1.address,
            v.token1.decimals,
            v.token1.symbol.clone(),
        ),
        state: LiveState::V3 {
            sqrt_price_x96: U256::ZERO,
            tick: 0,
            liquidity: U256::ZERO,
        },
        blockstamp: blockstamp(&fx),
        verified: true,
    });
    let id = v.entry.identity();
    assert!(mirror.apply_v3_swap(&id, sqrt, tick, liq, blockstamp(&fx)));

    let pool: Pool = mirror.get(&id).unwrap().to_core_pool();
    let req = DetectRequest {
        top_n: 10,
        max_hops: 4,
        incremental: false,
        chains: vec![],
        pools: vec![pool],
        cross_chain: None,
    };
    let json = req.to_engine_json().unwrap();
    let back: DetectRequest = serde_json::from_str(&json).unwrap();
    assert_eq!(req, back);

    let jv: Value = serde_json::from_str(&json).unwrap();
    let p = &jv["pools"][0];
    assert_eq!(p["kind"], "v3");
    assert_eq!(p["v3"]["sqrt_price_x96"], sqrt.to_string());
    assert_eq!(p["v3"]["liquidity"], liq.to_string());
    assert_eq!(p["v3"]["tick"].as_i64().unwrap() as i32, tick);
    assert!(p.get("v2").is_none());
}

#[test]
fn mint_burn_refresh_only_when_range_brackets_tick() {
    // Pure liquidity-refresh logic: a Mint in-range adds; a Burn out-of-range is
    // a no-op; a Burn in-range removes.
    let mirror = Mirror::new();
    let id = l2i_core::PoolAddress::Contract(addr("0x1111111111111111111111111111111111111111"));
    let bs = Blockstamp {
        chain_id: 1,
        number: 1,
        block_hash: B256::ZERO,
        timestamp: 1,
    };
    mirror.insert(PoolState {
        identity: id,
        kind: l2i_core::PoolKind::V3,
        fee_pips: 500,
        token0: l2i_core::Token::with_symbol(
            1,
            addr("0x0000000000000000000000000000000000000001"),
            18,
            "A",
        ),
        token1: l2i_core::Token::with_symbol(
            1,
            addr("0x0000000000000000000000000000000000000002"),
            18,
            "B",
        ),
        state: LiveState::V3 {
            sqrt_price_x96: U256::from(1u8),
            tick: 100,
            liquidity: U256::from(1000u64),
        },
        blockstamp: bs.clone(),
        verified: true,
    });

    // Mint bracketing tick 100 → +500.
    assert!(mirror.apply_v3_liquidity_change(&id, 0, 200, U256::from(500u64), true, bs.clone()));
    assert_eq!(liq(&mirror, &id), U256::from(1500u64));

    // Burn NOT bracketing tick 100 (range 200..300) → no change.
    assert!(mirror.apply_v3_liquidity_change(&id, 200, 300, U256::from(999u64), false, bs.clone()));
    assert_eq!(liq(&mirror, &id), U256::from(1500u64));

    // Burn bracketing tick 100 → -500.
    assert!(mirror.apply_v3_liquidity_change(&id, 50, 150, U256::from(500u64), false, bs));
    assert_eq!(liq(&mirror, &id), U256::from(1000u64));
}

fn liq(m: &Mirror, id: &l2i_core::PoolAddress) -> U256 {
    match m.get(id).unwrap().state {
        LiveState::V3 { liquidity, .. } => liquidity,
        _ => panic!(),
    }
}
