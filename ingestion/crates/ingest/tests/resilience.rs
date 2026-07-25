//! M9 reorg + reconcile exit criteria (deterministic / simulated, per
//! `docs/ARCHITECTURE.md §9` Tier-A item 5).

use alloy_primitives::{Address, B256, U256};
use l2i_core::{Blockstamp, PoolAddress, PoolKind, Token};
use l2i_ingest::mirror::{LiveState, Mirror, PoolState};
use l2i_ingest::reconcile::{reconcile_batch, reconcile_pool, reconcile_v2, ReconcileResult};
use l2i_ingest::reorg::{BlockRef, ReorgOutcome, ReorgTracker};
use l2i_rpc::mock::MockProvider;
use l2i_rpc::BlockId;

fn pool_state(addr: u8, r0: u64, r1: u64, number: u64, verified: bool) -> PoolState {
    PoolState {
        identity: PoolAddress::Contract(Address::from([addr; 20])),
        kind: PoolKind::V2,
        fee_pips: 3000,
        token0: Token::with_symbol(1, Address::from([1; 20]), 18, "A"),
        token1: Token::with_symbol(1, Address::from([2; 20]), 6, "B"),
        state: LiveState::V2 {
            reserve0: U256::from(r0),
            reserve1: U256::from(r1),
        },
        blockstamp: Blockstamp {
            chain_id: 1,
            number,
            block_hash: B256::from([number as u8; 32]),
            timestamp: number,
        },
        verified,
    }
}

fn bref(n: u64, h: u8, p: u8) -> BlockRef {
    BlockRef {
        number: n,
        hash: B256::from([h; 32]),
        parent_hash: B256::from([p; 32]),
    }
}

#[test]
fn reorg_rolls_back_marks_unverified_then_recovers() {
    let mirror = Mirror::new();
    // Pool B settled at block 100; pool A updated at block 101.
    mirror.insert(pool_state(0xB, 10, 20, 100, true));
    mirror.insert(pool_state(0xA, 30, 40, 101, true));
    let id_a = PoolAddress::Contract(Address::from([0xA; 20]));
    let id_b = PoolAddress::Contract(Address::from([0xB; 20]));

    let mut tracker = ReorgTracker::new(16);
    assert_eq!(tracker.observe(bref(100, 100, 99)), ReorgOutcome::Extended);
    assert_eq!(tracker.observe(bref(101, 101, 100)), ReorgOutcome::Extended);

    // A conflicting head at 102 whose parent != our block-101 hash → reorg to 100.
    let outcome = tracker.observe(bref(102, 250, 251));
    assert_eq!(
        outcome,
        ReorgOutcome::Reorg {
            common_ancestor: 100
        }
    );

    // Roll back: pools touched after block 100 go verified:false.
    let ReorgOutcome::Reorg { common_ancestor } = outcome else {
        unreachable!()
    };
    let affected = mirror.mark_unverified_after(common_ancestor);
    assert_eq!(affected, 1, "only pool A (block 101) is affected");
    assert!(!mirror.get(&id_a).unwrap().verified, "A rolled back");
    assert!(
        mirror.get(&id_b).unwrap().verified,
        "B (block 100) unaffected"
    );

    // No stale emission: the verified snapshot excludes the rolled-back pool.
    let snap = mirror.snapshot_verified();
    assert_eq!(snap.len(), 1);
    assert_eq!(snap[0].address, id_b);

    // Recover: re-derive A from canonical logs at the new block 102 → verified again.
    let new_stamp = Blockstamp {
        chain_id: 1,
        number: 102,
        block_hash: B256::from([250; 32]),
        timestamp: 102,
    };
    assert!(mirror.apply_v2_sync(&id_a, U256::from(31), U256::from(41), new_stamp));
    assert!(mirror.get(&id_a).unwrap().verified, "A recovered");
    assert_eq!(mirror.snapshot_verified().len(), 2);
}

fn get_reserves_response(r0: u64, r1: u64) -> Vec<u8> {
    let mut v = Vec::with_capacity(96);
    v.extend_from_slice(&U256::from(r0).to_be_bytes::<32>());
    v.extend_from_slice(&U256::from(r1).to_be_bytes::<32>());
    v.extend_from_slice(&[0u8; 32]); // blockTimestampLast
    v
}

#[tokio::test]
async fn reconcile_mismatch_flags_unverified_then_matches() {
    let mirror = Mirror::new();
    mirror.insert(pool_state(0xA, 100, 200, 500, true));
    let id = PoolAddress::Contract(Address::from([0xA; 20]));
    let addr = Address::from([0xA; 20]);
    let get_reserves = alloy_primitives::Bytes::from_static(&[0x09, 0x02, 0xf1, 0xac]);

    // 1) Independent read DIFFERS from the mirror → Mismatch → verified:false.
    let drifted =
        MockProvider::new(1).with_call(addr, get_reserves.clone(), get_reserves_response(150, 250));
    let r = reconcile_v2(
        &drifted,
        addr,
        (U256::from(100), U256::from(200)),
        BlockId::from(500u64),
    )
    .await
    .unwrap();
    assert_eq!(r, ReconcileResult::Mismatch);
    mirror.set_verified(&id, false);
    assert!(!mirror.get(&id).unwrap().verified);
    assert!(
        mirror.snapshot_verified().is_empty(),
        "drifted pool not emitted"
    );

    // 2) After re-seed, the read matches → Match → verified restored.
    let fresh = MockProvider::new(1).with_call(addr, get_reserves, get_reserves_response(100, 200));
    let r2 = reconcile_v2(
        &fresh,
        addr,
        (U256::from(100), U256::from(200)),
        BlockId::from(500u64),
    )
    .await
    .unwrap();
    assert_eq!(r2, ReconcileResult::Match);
    mirror.set_verified(&id, true);
    assert_eq!(mirror.snapshot_verified().len(), 1);
}

#[tokio::test]
async fn reconcile_pool_flips_verified_on_drift_at_pool_blockstamp() {
    // The live-path helper the ingestor calls: it reconciles a pool against the
    // chain AT THE POOL'S OWN BLOCKSTAMP and flips verified:false on mismatch — no
    // caller bookkeeping. Mirror pool settled at block 500 with reserves (100,200).
    let mirror = Mirror::new();
    mirror.insert(pool_state(0xA, 100, 200, 500, true));
    let id = PoolAddress::Contract(Address::from([0xA; 20]));
    let addr = Address::from([0xA; 20]);
    let get_reserves = alloy_primitives::Bytes::from_static(&[0x09, 0x02, 0xf1, 0xac]);
    let pool = mirror.get(&id).unwrap().to_core_pool();

    // Chain returns DIFFERENT reserves at block 500 → drift → verified:false + flagged.
    let drifted =
        MockProvider::new(1).with_call(addr, get_reserves.clone(), get_reserves_response(150, 250));
    let r = reconcile_pool(&drifted, &mirror, &pool).await.unwrap();
    assert_eq!(r, ReconcileResult::Mismatch);
    assert!(
        !mirror.get(&id).unwrap().verified,
        "drifted pool auto-flipped verified:false"
    );
    assert!(mirror.snapshot_verified().is_empty());

    // Chain matches the mirror at block 500 → Match, verified untouched.
    let intact = Mirror::new();
    intact.insert(pool_state(0xB, 100, 200, 500, true));
    let id_b = PoolAddress::Contract(Address::from([0xB; 20]));
    let addr_b = Address::from([0xB; 20]);
    let pool_b = intact.get(&id_b).unwrap().to_core_pool();
    let ok = MockProvider::new(1).with_call(addr_b, get_reserves, get_reserves_response(100, 200));
    let r2 = reconcile_pool(&ok, &intact, &pool_b).await.unwrap();
    assert_eq!(r2, ReconcileResult::Match);
    assert!(
        intact.get(&id_b).unwrap().verified,
        "matching pool stays verified"
    );
}

// ─────────────────────────── batched reconcile ──────────────────────────────

fn slot0_response(sqrt: u128, tick: i32) -> Vec<u8> {
    let mut v = vec![0u8; 64];
    v[0..32].copy_from_slice(&U256::from(sqrt).to_be_bytes::<32>());
    v[60..64].copy_from_slice(&tick.to_be_bytes()); // int24 tick, right-aligned
    v
}

fn liquidity_response(l: u128) -> Vec<u8> {
    U256::from(l).to_be_bytes::<32>().to_vec()
}

fn pool_state_v3(addr: u8, sqrt: u128, tick: i32, liq: u128, number: u64) -> PoolState {
    PoolState {
        identity: PoolAddress::Contract(Address::from([addr; 20])),
        kind: PoolKind::V3,
        fee_pips: 500,
        token0: Token::with_symbol(1, Address::from([1; 20]), 18, "A"),
        token1: Token::with_symbol(1, Address::from([2; 20]), 6, "B"),
        state: LiveState::V3 {
            sqrt_price_x96: U256::from(sqrt),
            tick,
            liquidity: U256::from(liq),
        },
        blockstamp: Blockstamp {
            chain_id: 1,
            number,
            block_hash: B256::from([number as u8; 32]),
            timestamp: number,
        },
        verified: true,
    }
}

#[tokio::test]
async fn reconcile_batch_flags_only_drift_in_one_multicall_per_block() {
    // Three V2 pools and a V3 pool, all stamped at the same block: reconciling them
    // must (a) cost exactly one multicall, (b) flip only the drifted pool, and (c)
    // count matches/mismatches exactly as the per-pool path would.
    let mirror = Mirror::new();
    mirror.insert(pool_state(0xA, 100, 200, 500, true)); // matches chain
    mirror.insert(pool_state(0xB, 100, 200, 500, true)); // will DRIFT
    mirror.insert(pool_state(0xC, 300, 400, 500, true)); // matches chain
    mirror.insert(pool_state_v3(0xD, 111, 7, 222, 500)); // matches chain

    let gr = alloy_primitives::Bytes::from_static(&[0x09, 0x02, 0xf1, 0xac]);
    let s0 = alloy_primitives::Bytes::from_static(&[0x38, 0x50, 0xc7, 0xbd]);
    let lq = alloy_primitives::Bytes::from_static(&[0x1a, 0x68, 0x65, 0x02]);
    let mock = MockProvider::new(1)
        .with_call(
            Address::from([0xA; 20]),
            gr.clone(),
            get_reserves_response(100, 200),
        )
        .with_call(
            Address::from([0xB; 20]),
            gr.clone(),
            get_reserves_response(150, 250),
        ) // drift
        .with_call(
            Address::from([0xC; 20]),
            gr,
            get_reserves_response(300, 400),
        )
        .with_call(Address::from([0xD; 20]), s0, slot0_response(111, 7))
        .with_call(Address::from([0xD; 20]), lq, liquidity_response(222));

    let pools = mirror.snapshot_verified();
    let before = mock.round_trips();
    let tally = reconcile_batch(&mock, &mirror, &pools).await;
    let used = mock.round_trips() - before;

    assert_eq!(tally.mismatched, 1, "only B drifted");
    assert_eq!(tally.matched, 3, "A, C, D match");
    assert_eq!(tally.failed, 0);
    assert_eq!(
        used, 1,
        "all four pools share block 500 → one multicall, not four-plus reads"
    );

    let v = |a: u8| {
        mirror
            .get(&PoolAddress::Contract(Address::from([a; 20])))
            .unwrap()
            .verified
    };
    assert!(!v(0xB), "drifted pool flipped verified:false");
    assert!(v(0xA) && v(0xC) && v(0xD), "matching pools stay verified");
}

#[tokio::test]
async fn reconcile_batch_uses_one_multicall_per_distinct_block() {
    // Pools stamped at different blocks can't share a multicall (each is read at its
    // own block), so the batch issues one request per distinct block — still far
    // fewer than one per pool.
    let mirror = Mirror::new();
    mirror.insert(pool_state(0xA, 100, 200, 500, true));
    mirror.insert(pool_state(0xB, 300, 400, 501, true));

    let gr = alloy_primitives::Bytes::from_static(&[0x09, 0x02, 0xf1, 0xac]);
    let mock = MockProvider::new(1)
        .with_call(
            Address::from([0xA; 20]),
            gr.clone(),
            get_reserves_response(100, 200),
        )
        .with_call(
            Address::from([0xB; 20]),
            gr,
            get_reserves_response(300, 400),
        );

    let pools = mirror.snapshot_verified();
    let before = mock.round_trips();
    let tally = reconcile_batch(&mock, &mirror, &pools).await;
    let used = mock.round_trips() - before;

    assert_eq!(tally.matched, 2);
    assert_eq!(tally.mismatched, 0);
    assert_eq!(used, 2, "two distinct blocks → two multicalls");
}
