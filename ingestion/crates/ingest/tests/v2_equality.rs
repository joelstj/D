//! M4 exit criterion — **event-derived reserves == `eth_call getReserves` at block
//! N**, exactly, over recorded real Arbitrum data.
//!
//! `fixtures/arbitrum_v2_sync.json`: the last `Sync` event in a real block N for a
//! real V2 pool (DIA/USD₮0), the `getReserves()` read at N, and a real
//! `aggregate3([getReserves])` seed response — all captured on-chain. The event we
//! decode must equal the independent `eth_call`, and seeding via multicall must
//! reproduce the same reserves; the emitted `Pool` must be valid contract JSON.

use alloy_primitives::{hex, Address, B256, U256};
use l2i_core::{Blockstamp, DetectRequest, Pool};
use l2i_ingest::event::decode_sync_reserves;
use l2i_ingest::mirror::Mirror;
use l2i_ingest::v2::seed_v2_pools;
use l2i_registry::gate::{ValidatedPool, ValidatedToken};
use l2i_registry::schema::{PoolEntry, V2V3Entry};
use l2i_rpc::mock::MockProvider;
use l2i_rpc::BlockId;
use serde_json::Value;

fn fixture() -> Value {
    let raw = std::fs::read_to_string(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/fixtures/arbitrum_v2_sync.json"
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
        entry: PoolEntry::V2(V2V3Entry {
            dex: "uniswap_v2".into(),
            address: addr(fx["pool"].as_str().unwrap()),
            fee_pips: 3000,
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
        fee_pips: 3000,
    }
}

#[test]
fn event_derived_reserves_equal_get_reserves_exactly() {
    let fx = fixture();
    // Event-derived: decode the real Sync log's data.
    let sync_data = bytes(fx["sync_log"]["data"].as_str().unwrap());
    let (ev0, ev1) = decode_sync_reserves(&sync_data).unwrap();
    // Independent eth_call getReserves at the same block.
    let call0 = u256(fx["getreserves"]["reserve0"].as_str().unwrap());
    let call1 = u256(fx["getreserves"]["reserve1"].as_str().unwrap());
    assert_eq!(ev0, call0, "event reserve0 != getReserves reserve0");
    assert_eq!(ev1, call1, "event reserve1 != getReserves reserve1");
}

#[tokio::test]
async fn multicall_seed_reproduces_reserves() {
    let fx = fixture();
    let seed = &fx["seed_multicall"];
    let mock = MockProvider::new(42161).with_call(
        addr(seed["target"].as_str().unwrap()),
        bytes(seed["calldata"].as_str().unwrap()),
        bytes(seed["response"].as_str().unwrap()),
    );
    let seeded = seed_v2_pools(
        &mock,
        &[validated(&fx)],
        blockstamp(&fx),
        BlockId::from(fx["block"].as_u64().unwrap()),
    )
    .await
    .unwrap();
    assert_eq!(seeded.len(), 1);
    let call0 = u256(fx["getreserves"]["reserve0"].as_str().unwrap());
    let call1 = u256(fx["getreserves"]["reserve1"].as_str().unwrap());
    match &seeded[0].state {
        l2i_ingest::LiveState::V2 { reserve0, reserve1 } => {
            assert_eq!(*reserve0, call0);
            assert_eq!(*reserve1, call1);
        }
        _ => panic!("expected V2 state"),
    }
    assert!(seeded[0].verified);
}

#[test]
fn emitted_pool_is_valid_contract_json() {
    let fx = fixture();
    let sync_data = bytes(fx["sync_log"]["data"].as_str().unwrap());
    let (ev0, ev1) = decode_sync_reserves(&sync_data).unwrap();

    // Seed the mirror, then apply the Sync as the live update.
    let mirror = Mirror::new();
    let mut seed = validated(&fx);
    // Insert a seeded entry (reserves from getReserves), then Sync overwrites it.
    let call0 = u256(fx["getreserves"]["reserve0"].as_str().unwrap());
    let call1 = u256(fx["getreserves"]["reserve1"].as_str().unwrap());
    mirror.insert(l2i_ingest::PoolState {
        identity: seed.entry.identity(),
        kind: l2i_core::PoolKind::V2,
        fee_pips: 3000,
        token0: l2i_core::Token::with_symbol(
            42161,
            seed.token0.address,
            seed.token0.decimals,
            std::mem::take(&mut seed.token0.symbol),
        ),
        token1: l2i_core::Token::with_symbol(
            42161,
            seed.token1.address,
            seed.token1.decimals,
            std::mem::take(&mut seed.token1.symbol),
        ),
        state: l2i_ingest::LiveState::V2 {
            reserve0: call0,
            reserve1: call1,
        },
        blockstamp: blockstamp(&fx),
        verified: true,
    });
    let id = validated(&fx).entry.identity();
    assert!(mirror.apply_v2_sync(&id, ev0, ev1, blockstamp(&fx)));

    let pool: Pool = mirror.get(&id).unwrap().to_core_pool();

    // Build a DetectRequest around it and round-trip through the contract JSON.
    let req = DetectRequest {
        top_n: 10,
        max_hops: 4,
        incremental: false,
        chains: vec![],
        pools: vec![pool.clone()],
        cross_chain: None,
    };
    let json = req.to_engine_json().unwrap();
    let back: DetectRequest = serde_json::from_str(&json).unwrap();
    assert_eq!(req, back);

    // Shape checks: kind "v2", reserves as decimal strings equal to the event.
    let v: Value = serde_json::from_str(&json).unwrap();
    let p = &v["pools"][0];
    assert_eq!(p["kind"], "v2");
    assert_eq!(p["verified"], true);
    assert_eq!(p["v2"]["reserve0"], ev0.to_string());
    assert_eq!(p["v2"]["reserve1"], ev1.to_string());
    assert!(p.get("v3").is_none());
}
