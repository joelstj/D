//! Hot-path microbenchmarks (`docs/ARCHITECTURE.md §8`): the intra-process work
//! from a pushed event to a built `DetectRequest`. The §8 budget is p99 < 5 ms for
//! decode→emit; these record where each stage sits. The live p99-within-budget gate
//! is Tier-B (runs against real endpoints); this records p50/p99 deterministically.

use alloy_primitives::{Address, B256, U256};
use criterion::{criterion_group, criterion_main, Criterion};
use l2i_aggregator::request::{build_detect_request, ChainSnapshot, RequestConfig};
use l2i_aggregator::snapshot::{re_stamp, IncrementalTracker};
use l2i_amm::v2::get_amount_out;
use l2i_core::{Blockstamp, ChainContext, DecU256, Pool, PoolAddress, PoolKind, Token, V2State};
use l2i_ingest::event::decode_sync_reserves;
use l2i_ingest::mirror::Mirror;
use std::collections::BTreeMap;
use std::hint::black_box;

fn sync_data(r0: u128, r1: u128) -> [u8; 64] {
    let mut d = [0u8; 64];
    d[16..32].copy_from_slice(&r0.to_be_bytes());
    d[48..64].copy_from_slice(&r1.to_be_bytes());
    d
}

fn pool(i: u8) -> Pool {
    Pool {
        address: PoolAddress::Contract(Address::from([i; 20])),
        kind: PoolKind::V2,
        fee_pips: 3000,
        verified: true,
        token0: Token::with_symbol(42161, Address::from([1; 20]), 18, "A"),
        token1: Token::with_symbol(42161, Address::from([2; 20]), 6, "B"),
        blockstamp: Blockstamp {
            chain_id: 42161,
            number: 1,
            block_hash: B256::ZERO,
            timestamp: 1,
        },
        v2: Some(V2State {
            reserve0: DecU256(U256::from(1_000_000u64)),
            reserve1: DecU256(U256::from(2_000_000u64)),
        }),
        v3: None,
    }
}

fn chain_ctx() -> ChainContext {
    ChainContext {
        chain_id: 42161,
        gas_price_wei: 10_000_000,
        l1_data_fee_wei: 0,
        base_gas: 150_000,
        per_hop_gas: 100_000,
        gas_safety_multiplier: 1.5,
        min_profit_bps: 5.0,
        native_price_in: BTreeMap::new(),
        hubs: vec![],
    }
}

fn bench_decode(c: &mut Criterion) {
    let data = sync_data(1_234_567_890, 9_876_543_210);
    c.bench_function("decode_sync", |b| {
        b.iter(|| decode_sync_reserves(black_box(&data)).unwrap())
    });
}

fn bench_amm(c: &mut Criterion) {
    let (a, ri, ro) = (
        U256::from(10u64).pow(U256::from(18u64)),
        U256::from(10u64).pow(U256::from(21u64)),
        U256::from(3u64) * U256::from(10u64).pow(U256::from(9u64)),
    );
    c.bench_function("v2_get_amount_out", |b| {
        b.iter(|| get_amount_out(black_box(a), black_box(ri), black_box(ro), 3000))
    });
}

fn bench_mirror_apply(c: &mut Criterion) {
    let mirror = Mirror::new();
    mirror.insert(l2i_ingest::PoolState {
        identity: PoolAddress::Contract(Address::from([7; 20])),
        kind: PoolKind::V2,
        fee_pips: 3000,
        token0: Token::with_symbol(42161, Address::from([1; 20]), 18, "A"),
        token1: Token::with_symbol(42161, Address::from([2; 20]), 6, "B"),
        state: l2i_ingest::LiveState::V2 {
            reserve0: U256::from(1u64),
            reserve1: U256::from(1u64),
        },
        blockstamp: Blockstamp {
            chain_id: 42161,
            number: 1,
            block_hash: B256::ZERO,
            timestamp: 1,
        },
        verified: true,
    });
    let id = PoolAddress::Contract(Address::from([7; 20]));
    let stamp = Blockstamp {
        chain_id: 42161,
        number: 2,
        block_hash: B256::from([2; 32]),
        timestamp: 2,
    };
    c.bench_function("mirror_apply_v2_sync", |b| {
        b.iter(|| {
            mirror.apply_v2_sync(
                black_box(&id),
                U256::from(123u64),
                U256::from(456u64),
                stamp.clone(),
            )
        })
    });
}

fn bench_build_request(c: &mut Criterion) {
    let pools: Vec<Pool> = (0..64u8).map(pool).collect();
    let head = Blockstamp {
        chain_id: 42161,
        number: 100,
        block_hash: B256::from([9; 32]),
        timestamp: 100,
    };
    c.bench_function("snapshot_and_build_request_64pools", |b| {
        b.iter(|| {
            let stamped = re_stamp(&head, pools.clone());
            let mut tracker = IncrementalTracker::new();
            let changed = tracker.changed(&stamped);
            build_detect_request(
                vec![ChainSnapshot {
                    context: chain_ctx(),
                    pools: changed,
                }],
                true,
                None,
                RequestConfig::default(),
            )
        })
    });
}

criterion_group!(
    hotpath,
    bench_decode,
    bench_amm,
    bench_mirror_apply,
    bench_build_request
);
criterion_main!(hotpath);
