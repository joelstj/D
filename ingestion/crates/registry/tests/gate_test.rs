//! Validation-gate tests over **recorded real** on-chain reads at a pinned block.
//!
//! `fixtures/arbitrum_gate.json` holds genuine Arbitrum One `eth_call`/`eth_getCode`
//! responses (a real Uniswap V3 WETH/USDC 500 pool, a real Camelot V2 WETH/USDC
//! pair, and the WETH/USDC token metadata) captured at a pinned block. The gate
//! runs against a `MockProvider` replaying exactly those responses — deterministic,
//! yet every value is real. `fixtures/unichain_v4_initialize.json` is a real
//! Uniswap V4 `Initialize` event (a genuine `PoolKey → poolId` pair) used to prove
//! the `poolId` computation against chain and exercise the hook gate.

use alloy_primitives::{hex, Address, Bytes, B256};
use l2i_registry::abi::compute_pool_id;
use l2i_registry::gate::{validate_pool, validate_registry, GatePolicy};
use l2i_registry::schema::{PoolEntry, PoolRegistry, V2V3Entry, V4Entry};
use l2i_registry::RejectReason;
use l2i_rpc::mock::MockProvider;
use l2i_rpc::BlockId;
use serde_json::Value;
use std::collections::HashSet;

fn load(name: &str) -> Value {
    let raw = std::fs::read_to_string(format!(
        "{}/tests/fixtures/{name}",
        env!("CARGO_MANIFEST_DIR")
    ))
    .unwrap();
    serde_json::from_str(&raw).unwrap()
}

fn addr(s: &str) -> Address {
    s.parse().unwrap()
}

fn mock_from(fx: &Value, chain_id: u64) -> MockProvider {
    let mut m = MockProvider::new(chain_id);
    for r in fx["reads"].as_array().unwrap() {
        let to = addr(r["to"].as_str().unwrap());
        let data = Bytes::from(hex::decode(r["calldata"].as_str().unwrap()).unwrap());
        let ret = Bytes::from(hex::decode(r["result"].as_str().unwrap()).unwrap());
        m = m.with_call(to, data, ret);
    }
    for c in fx["code"].as_array().unwrap() {
        m = m.with_contract(addr(c.as_str().unwrap()));
    }
    m
}

fn at(fx: &Value) -> BlockId {
    BlockId::from(fx["block"].as_u64().unwrap())
}

fn v3_entry(fx: &Value) -> V2V3Entry {
    let p = &fx["pools"]["v3"];
    V2V3Entry {
        dex: "uniswap_v3".into(),
        address: addr(p["address"].as_str().unwrap()),
        fee_pips: p["fee"].as_u64().unwrap() as u32,
        token0: addr(p["token0"].as_str().unwrap()),
        token1: addr(p["token1"].as_str().unwrap()),
        factory: Some(addr(p["factory"].as_str().unwrap())),
    }
}

// ─────────────────────────── V2/V3 happy path ───────────────────────────

#[tokio::test]
async fn real_v3_pool_validates_and_metadata_matches_chain() {
    let fx = load("arbitrum_gate.json");
    let mock = mock_from(&fx, 42161);
    let policy = GatePolicy {
        check_factory: true,
        ..Default::default()
    };
    let ok = validate_pool(&mock, PoolEntry::V3(v3_entry(&fx)), at(&fx), &policy)
        .await
        .expect("real V3 pool should validate");
    // decimals/symbol come from the fork read, not a guess.
    assert_eq!(ok.token0.decimals, 18);
    assert_eq!(ok.token0.symbol, "WETH");
    assert_eq!(ok.token1.decimals, 6);
    assert_eq!(ok.token1.symbol, "USDC");
    assert_eq!(ok.fee_pips, 500);
}

#[tokio::test]
async fn real_v2_pool_validates() {
    let fx = load("arbitrum_gate.json");
    let mock = mock_from(&fx, 42161);
    let p = &fx["pools"]["v2"];
    let entry = PoolEntry::V2(V2V3Entry {
        dex: "camelot_v2".into(),
        address: addr(p["address"].as_str().unwrap()),
        fee_pips: 3000,
        token0: addr(p["token0"].as_str().unwrap()),
        token1: addr(p["token1"].as_str().unwrap()),
        factory: Some(addr(p["factory"].as_str().unwrap())),
    });
    let ok = validate_pool(
        &mock,
        entry,
        at(&fx),
        &GatePolicy {
            check_factory: true,
            ..Default::default()
        },
    )
    .await
    .expect("real V2 pair should validate");
    assert_eq!(ok.token0.decimals, 18);
    assert_eq!(ok.token1.decimals, 6);
}

// ─────────────────────────── V2/V3 rejections ───────────────────────────

#[tokio::test]
async fn rejects_wrong_token0() {
    let fx = load("arbitrum_gate.json");
    let mock = mock_from(&fx, 42161);
    let mut e = v3_entry(&fx);
    e.token0 = addr("0x000000000000000000000000000000000000dEaD");
    let r = validate_pool(&mock, PoolEntry::V3(e), at(&fx), &GatePolicy::default())
        .await
        .unwrap_err();
    assert!(
        matches!(r.reason, RejectReason::Token0Mismatch { .. }),
        "{}",
        r.reason
    );
}

#[tokio::test]
async fn rejects_wrong_fee() {
    let fx = load("arbitrum_gate.json");
    let mock = mock_from(&fx, 42161);
    let mut e = v3_entry(&fx);
    e.fee_pips = 3000; // real pool is 500
    let r = validate_pool(&mock, PoolEntry::V3(e), at(&fx), &GatePolicy::default())
        .await
        .unwrap_err();
    assert!(
        matches!(
            r.reason,
            RejectReason::FeeMismatch {
                declared: 3000,
                onchain: 500
            }
        ),
        "{}",
        r.reason
    );
}

#[tokio::test]
async fn rejects_non_contract() {
    let fx = load("arbitrum_gate.json");
    let mock = mock_from(&fx, 42161);
    let mut e = v3_entry(&fx);
    // An address with no recorded code → NotAContract, before any call.
    e.address = addr("0x00000000000000000000000000000000C0FFEE00");
    let r = validate_pool(&mock, PoolEntry::V3(e), at(&fx), &GatePolicy::default())
        .await
        .unwrap_err();
    assert!(
        matches!(r.reason, RejectReason::NotAContract(_)),
        "{}",
        r.reason
    );
}

#[tokio::test]
async fn rejects_denied_token() {
    let fx = load("arbitrum_gate.json");
    let mock = mock_from(&fx, 42161);
    let e = v3_entry(&fx);
    let mut deny = HashSet::new();
    deny.insert(e.token1); // deny USDC
    let policy = GatePolicy {
        deny_list: deny,
        ..Default::default()
    };
    let r = validate_pool(&mock, PoolEntry::V3(e), at(&fx), &policy)
        .await
        .unwrap_err();
    assert!(
        matches!(r.reason, RejectReason::DeniedToken(_)),
        "{}",
        r.reason
    );
}

#[tokio::test]
async fn rejects_wrong_factory() {
    let fx = load("arbitrum_gate.json");
    let mock = mock_from(&fx, 42161);
    let mut e = v3_entry(&fx);
    e.factory = Some(addr("0x000000000000000000000000000000000000BEEF"));
    let policy = GatePolicy {
        check_factory: true,
        ..Default::default()
    };
    let r = validate_pool(&mock, PoolEntry::V3(e), at(&fx), &policy)
        .await
        .unwrap_err();
    assert!(
        matches!(r.reason, RejectReason::FactoryMismatch { .. }),
        "{}",
        r.reason
    );
}

// ─────────────────────────────── V4 gate ────────────────────────────────

fn v4_entry(fx: &Value) -> V4Entry {
    V4Entry {
        dex: "uniswap_v4".into(),
        id: fx["poolId"].as_str().unwrap().parse().unwrap(),
        currency0: addr(fx["currency0"].as_str().unwrap()),
        currency1: addr(fx["currency1"].as_str().unwrap()),
        fee: fx["fee"].as_u64().unwrap() as u32,
        tick_spacing: fx["tick_spacing"].as_i64().unwrap() as i32,
        hooks: addr(fx["hooks"].as_str().unwrap()),
    }
}

#[test]
fn pool_id_computation_matches_real_chain_value() {
    // Prove poolId = keccak256(abi.encode(PoolKey)) against a real Unichain V4
    // Initialize event.
    let fx = load("unichain_v4_initialize.json");
    let e = v4_entry(&fx);
    let computed = compute_pool_id(e.currency0, e.currency1, e.fee, e.tick_spacing, e.hooks);
    let real: B256 = fx["poolId"].as_str().unwrap().parse().unwrap();
    assert_eq!(
        computed, real,
        "computed poolId must equal the on-chain poolId"
    );
}

#[tokio::test]
async fn rejects_unsafe_v4_hook() {
    let fx = load("unichain_v4_initialize.json");
    let mock = mock_from(&fx, 130);
    let e = v4_entry(&fx); // real hook is non-zero and not safe-listed
    assert_ne!(
        e.hooks,
        Address::ZERO,
        "fixture must have a non-zero hook to test this"
    );
    let r = validate_pool(&mock, PoolEntry::V4(e), at_v4(&fx), &GatePolicy::default())
        .await
        .unwrap_err();
    assert!(
        matches!(r.reason, RejectReason::UnsafeHook(_)),
        "{}",
        r.reason
    );
}

#[tokio::test]
async fn accepts_safelisted_v4_hook_with_real_poolid() {
    let fx = load("unichain_v4_initialize.json");
    let mock = mock_from(&fx, 130);
    let e = v4_entry(&fx);
    let mut safe = HashSet::new();
    safe.insert(e.hooks);
    let policy = GatePolicy {
        safe_hooks: safe,
        ..Default::default()
    };
    let ok = validate_pool(&mock, PoolEntry::V4(e), at_v4(&fx), &policy)
        .await
        .expect("safe-listed hook + real poolId should validate");
    // Dynamic-fee sentinel retained; currency metadata read from chain.
    assert_eq!(ok.fee_pips, l2i_registry::schema::DYNAMIC_FEE_FLAG);
    assert_eq!(
        ok.token0.decimals,
        fx["currency0_decimals"].as_u64().unwrap() as u8
    );
}

#[tokio::test]
async fn rejects_v4_poolid_mismatch() {
    let fx = load("unichain_v4_initialize.json");
    let mock = mock_from(&fx, 130);
    let mut e = v4_entry(&fx);
    let mut safe = HashSet::new();
    safe.insert(e.hooks);
    // Tamper the declared id so it no longer matches the PoolKey.
    let mut id = e.id.0;
    id[0] ^= 0xff;
    e.id = B256::from(id);
    let policy = GatePolicy {
        safe_hooks: safe,
        ..Default::default()
    };
    let r = validate_pool(&mock, PoolEntry::V4(e), at_v4(&fx), &policy)
        .await
        .unwrap_err();
    assert!(
        matches!(r.reason, RejectReason::PoolIdMismatch { .. }),
        "{}",
        r.reason
    );
}

fn at_v4(fx: &Value) -> BlockId {
    BlockId::from(fx["read_block"].as_u64().unwrap())
}

// ─────────────────────── batched registry validation ────────────────────────

#[tokio::test]
async fn batched_registry_matches_per_pool_gate_and_collapses_round_trips() {
    let fx = load("arbitrum_gate.json");
    let mock = mock_from(&fx, 42161);
    let policy = GatePolicy {
        check_factory: true,
        ..Default::default()
    };

    // A diverse registry exercising accept + every rejection kind, all against the
    // same recorded real reads (each rejection is tampered *declared* data, so the
    // on-chain reads still resolve — no synthetic reads anywhere).
    let p2 = &fx["pools"]["v2"];
    let good_v2 = PoolEntry::V2(V2V3Entry {
        dex: "camelot_v2".into(),
        address: addr(p2["address"].as_str().unwrap()),
        fee_pips: 3000,
        token0: addr(p2["token0"].as_str().unwrap()),
        token1: addr(p2["token1"].as_str().unwrap()),
        factory: Some(addr(p2["factory"].as_str().unwrap())),
    });
    let mut wrong_t0 = v3_entry(&fx);
    wrong_t0.token0 = addr("0x000000000000000000000000000000000000dEaD");
    let mut non_contract = v3_entry(&fx);
    non_contract.address = addr("0x00000000000000000000000000000000C0FFEE00");
    let mut wrong_factory = v3_entry(&fx);
    wrong_factory.factory = Some(addr("0x000000000000000000000000000000000000BEEF"));

    let registry = PoolRegistry {
        pools: vec![
            PoolEntry::V3(v3_entry(&fx)), // accepts
            good_v2,                      // accepts
            PoolEntry::V3(wrong_t0),      // Token0Mismatch
            PoolEntry::V3(non_contract),  // NotAContract
            PoolEntry::V3(wrong_factory), // FactoryMismatch
        ],
    };

    // Reference: the per-pool gate, looped, straight against the live mock.
    let mut ref_accepted = Vec::new();
    let mut ref_rejected = Vec::new();
    for e in &registry.pools {
        match validate_pool(&mock, e.clone(), at(&fx), &policy).await {
            Ok(v) => ref_accepted.push(v),
            Err(r) => ref_rejected.push(r),
        }
    }
    let per_call_round_trips = mock.round_trips();

    // Batched: measure only the requests the batched gate itself issues.
    let before = mock.round_trips();
    let outcome = validate_registry(&mock, &registry, at(&fx), &policy).await;
    let batched_round_trips = mock.round_trips() - before;

    // Identical decisions and reasons — Phase 2 is literally the same code path.
    assert_eq!(
        outcome.accepted, ref_accepted,
        "accepted set must match the per-pool gate exactly"
    );
    assert_eq!(
        outcome.rejected, ref_rejected,
        "rejections and their reasons must match the per-pool gate exactly"
    );
    assert_eq!(outcome.accepted.len(), 2, "two pools should validate");
    assert_eq!(outcome.rejected.len(), 3, "three pools should be rejected");

    // O(1) requests: one eth_getCode batch + one multicall chunk, no matter how many
    // pools — and a large reduction versus the per-call path.
    assert!(
        batched_round_trips <= 2,
        "batched gate issued {batched_round_trips} round-trips; expected <= 2"
    );
    assert!(
        per_call_round_trips > batched_round_trips * 5,
        "batching should cut requests many-fold (per-call {per_call_round_trips} vs batched {batched_round_trips})"
    );
}

#[tokio::test]
async fn batched_registry_validates_v4_pools() {
    // V4 pools have no contract address (identity is the poolId), so batching is
    // purely their currency `decimals`/`symbol` reads through one multicall.
    let fx = load("unichain_v4_initialize.json");
    let mock = mock_from(&fx, 130);
    let e = v4_entry(&fx);
    let mut safe = HashSet::new();
    safe.insert(e.hooks);
    let policy = GatePolicy {
        safe_hooks: safe,
        ..Default::default()
    };
    let registry = PoolRegistry {
        pools: vec![PoolEntry::V4(e)],
    };

    let before = mock.round_trips();
    let outcome = validate_registry(&mock, &registry, at_v4(&fx), &policy).await;
    let used = mock.round_trips() - before;

    assert_eq!(
        outcome.accepted.len(),
        1,
        "safe-hook V4 pool should validate"
    );
    assert_eq!(
        outcome.accepted[0].token0.decimals,
        fx["currency0_decimals"].as_u64().unwrap() as u8
    );
    assert!(
        used <= 1,
        "V4-only batch needs at most one multicall (no getCode); used {used}"
    );
}
