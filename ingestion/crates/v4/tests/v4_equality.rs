//! M6 exit criteria over **recorded real** Unichain V4 data.
//!
//! `fixtures/unichain_v4_swap.json`: the block's last `PoolManager.Swap` for a real
//! poolId, `StateView.getSlot0`/`getLiquidity` at N, a real `aggregate3` seed, and
//! (`m2_pool_state`) the live StateView state of the M2 registry fixture's real V4
//! pool. The M2 fixture (`../registry/.../unichain_v4_initialize.json`) supplies
//! that pool's real `PoolKey` (currencies, dynamic-fee flag, hook).

use alloy_primitives::{hex, Address, B256, U256};
use l2i_core::{Blockstamp, DetectRequest, Pool, PoolAddress, PoolKind, Token};
use l2i_ingest::mirror::{LiveState, Mirror, PoolState};
use l2i_registry::gate::{ValidatedPool, ValidatedToken};
use l2i_registry::schema::{V4Entry, DYNAMIC_FEE_FLAG};
use l2i_rpc::mock::MockProvider;
use l2i_rpc::BlockId;
use l2i_v4::event::decode_v4_swap_parts;
use l2i_v4::stateview::seed_v4_pools;
use serde_json::Value;

fn load(path: &str) -> Value {
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}
fn fixture() -> Value {
    load(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/fixtures/unichain_v4_swap.json"
    ))
}
fn m2_fixture() -> Value {
    load(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../registry/tests/fixtures/unichain_v4_initialize.json"
    ))
}
fn u256(s: &str) -> U256 {
    U256::from_str_radix(s, 10).unwrap()
}
fn addr(s: &str) -> Address {
    s.parse().unwrap()
}
fn b256(s: &str) -> B256 {
    s.parse().unwrap()
}
fn bytes(s: &str) -> Vec<u8> {
    hex::decode(s).unwrap()
}

#[test]
fn v4_event_state_equals_stateview_exactly() {
    let fx = fixture();
    let pid = b256(fx["poolId"].as_str().unwrap());
    let data = bytes(fx["swap_log"]["data"].as_str().unwrap());
    let s = decode_v4_swap_parts(pid, &data).unwrap();
    let sv = &fx["stateview"];
    assert_eq!(
        s.sqrt_price_x96,
        u256(sv["sqrt_price_x96"].as_str().unwrap()),
        "sqrtPriceX96"
    );
    assert_eq!(
        s.liquidity,
        u256(sv["liquidity"].as_str().unwrap()),
        "liquidity"
    );
    assert_eq!(s.tick, sv["tick"].as_i64().unwrap() as i32, "tick");
    // The Swap event's fee field is the effective (dynamic) fee for this block.
    assert_eq!(s.fee, fx["event"]["fee"].as_u64().unwrap() as u32);
}

#[tokio::test]
async fn stateview_multicall_seed_reproduces_state() {
    let fx = fixture();
    let seed = &fx["seed_multicall"];
    let mock = MockProvider::new(130).with_call(
        addr(seed["target"].as_str().unwrap()),
        bytes(seed["calldata"].as_str().unwrap()),
        bytes(seed["response"].as_str().unwrap()),
    );
    // Seed reads StateView purely by poolId; currency metadata is passthrough
    // (validated on-chain in M2), so illustrative currencies are fine here — the
    // assertion is on the *state*.
    let pid = b256(fx["poolId"].as_str().unwrap());
    let vp = ValidatedPool {
        entry: l2i_registry::schema::PoolEntry::V4(V4Entry {
            dex: "uniswap_v4".into(),
            id: pid,
            currency0: addr("0x0000000000000000000000000000000000000001"),
            currency1: addr("0x0000000000000000000000000000000000000002"),
            fee: 2500,
            tick_spacing: 60,
            hooks: Address::ZERO,
        }),
        token0: ValidatedToken {
            address: addr("0x0000000000000000000000000000000000000001"),
            decimals: 18,
            symbol: "A".into(),
        },
        token1: ValidatedToken {
            address: addr("0x0000000000000000000000000000000000000002"),
            decimals: 18,
            symbol: "B".into(),
        },
        fee_pips: 2500,
    };
    let seeded = seed_v4_pools(
        &mock,
        &[vp],
        addr(fx["state_view"].as_str().unwrap()),
        Blockstamp {
            chain_id: 130,
            number: fx["block"].as_u64().unwrap(),
            block_hash: b256(fx["block_hash"].as_str().unwrap()),
            timestamp: fx["timestamp"].as_u64().unwrap(),
        },
        BlockId::from(fx["block"].as_u64().unwrap()),
    )
    .await
    .unwrap();
    assert_eq!(seeded.len(), 1);
    assert_eq!(seeded[0].kind, PoolKind::V3, "V4 emits as v3");
    assert_eq!(
        seeded[0].identity,
        PoolAddress::PoolId(pid),
        "identity is the poolId"
    );
    let sv = &fx["stateview"];
    match &seeded[0].state {
        LiveState::V3 {
            sqrt_price_x96,
            tick,
            liquidity,
        } => {
            assert_eq!(
                *sqrt_price_x96,
                u256(sv["sqrt_price_x96"].as_str().unwrap())
            );
            assert_eq!(*liquidity, u256(sv["liquidity"].as_str().unwrap()));
            assert_eq!(*tick, sv["tick"].as_i64().unwrap() as i32);
        }
        _ => panic!("expected V3-shaped state"),
    }
}

#[test]
fn emits_v3_with_poolid_and_dynamic_fee() {
    // Real M2 V4 pool: dynamic-fee (0x800000), real currencies, real live state.
    let m2 = m2_fixture();
    let fx = fixture();
    let m2s = &fx["m2_pool_state"];
    let pid = b256(m2["poolId"].as_str().unwrap());
    assert_eq!(
        m2["fee"].as_u64().unwrap() as u32,
        DYNAMIC_FEE_FLAG,
        "M2 pool is dynamic-fee"
    );

    let cur0 = addr(m2["currency0"].as_str().unwrap());
    let cur1 = addr(m2["currency1"].as_str().unwrap());
    let bs = Blockstamp {
        chain_id: 130,
        number: m2s["block"].as_u64().unwrap(),
        block_hash: b256(m2s["block_hash"].as_str().unwrap()),
        timestamp: m2s["timestamp"].as_u64().unwrap(),
    };
    let mirror = Mirror::new();
    mirror.insert(PoolState {
        identity: PoolAddress::PoolId(pid),
        kind: PoolKind::V3,
        // seed fee = live lp_fee (dynamic pool).
        fee_pips: m2s["lp_fee"].as_u64().unwrap() as u32,
        token0: Token::with_symbol(
            130,
            cur0,
            m2["currency0_decimals"].as_u64().unwrap() as u8,
            m2["currency0_symbol"].as_str().unwrap(),
        ),
        token1: Token::with_symbol(
            130,
            cur1,
            m2["currency1_decimals"].as_u64().unwrap() as u8,
            m2["currency1_symbol"].as_str().unwrap(),
        ),
        state: LiveState::V3 {
            sqrt_price_x96: u256(m2s["sqrt_price_x96"].as_str().unwrap()),
            tick: m2s["tick"].as_i64().unwrap() as i32,
            liquidity: u256(m2s["liquidity"].as_str().unwrap()),
        },
        blockstamp: bs.clone(),
        verified: true,
    });
    let id = PoolAddress::PoolId(pid);

    // A live V4 Swap on a dynamic-fee pool sets the effective fee from the event.
    let event_fee = fx["event"]["fee"].as_u64().unwrap() as u32; // 2500
    let swap = l2i_v4::event::V4SwapState {
        pool_id: pid,
        sqrt_price_x96: u256(m2s["sqrt_price_x96"].as_str().unwrap()),
        liquidity: u256(m2s["liquidity"].as_str().unwrap()),
        tick: m2s["tick"].as_i64().unwrap() as i32,
        fee: event_fee,
    };
    assert!(l2i_v4::apply_v4_swap(&mirror, &swap, DYNAMIC_FEE_FLAG, bs));

    let pool: Pool = mirror.get(&id).unwrap().to_core_pool();
    assert_eq!(
        pool.fee_pips, event_fee,
        "dynamic fee read from the Swap event"
    );

    // Emitted as v3 with the poolId (0x + 64 hex) as its address.
    let req = DetectRequest {
        top_n: 10,
        max_hops: 4,
        incremental: false,
        chains: vec![],
        pools: vec![pool],
        cross_chain: None,
    };
    let json = req.to_engine_json().unwrap();
    let v: Value = serde_json::from_str(&json).unwrap();
    let p = &v["pools"][0];
    assert_eq!(p["kind"], "v3");
    assert_eq!(
        p["address"].as_str().unwrap(),
        m2["poolId"].as_str().unwrap()
    );
    assert_eq!(p["fee_pips"].as_u64().unwrap() as u32, event_fee);
    assert!(p["v3"].is_object());
}
