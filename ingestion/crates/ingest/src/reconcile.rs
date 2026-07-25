//! Background reconciliation (`docs/ARCHITECTURE.md §5–6`).
//!
//! Periodically we take a subset of pools and *independently* `eth_call` their
//! state at a pinned block, asserting equality with the event-derived mirror. A
//! mismatch means our mirror drifted (a missed log, a silent reorg): the pool goes
//! `verified:false` and is re-seeded. This is the continuous proof our data is real
//! — the same equality the M4/M5/M6 fixtures assert, run live and forever.

use crate::error::Result;
use crate::mirror::Mirror;
use crate::v2::{decode_reserves, get_reserves_calldata};
use crate::v3::{decode_liquidity, decode_slot0, liquidity_calldata, slot0_calldata};
use alloy_primitives::{Address, U256};
use l2i_core::Pool;
use l2i_rpc::multicall::Call3;
use l2i_rpc::{BlockId, ChainProvider};
use std::collections::BTreeMap;

/// Whether an independent read matched the mirror.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ReconcileResult {
    /// The independent read equals the mirror — `verified` stays true.
    Match,
    /// The independent read differs — the pool must go `verified:false` + re-seed.
    Mismatch,
}

/// Reconcile a V2 pool: read `getReserves` at `block` and compare to `expected`.
pub async fn reconcile_v2<P: ChainProvider + ?Sized>(
    provider: &P,
    address: Address,
    expected: (U256, U256),
    block: BlockId,
) -> Result<ReconcileResult> {
    let ret = provider
        .call(address, get_reserves_calldata(), block)
        .await?;
    let got = decode_reserves(&ret)?;
    Ok(if got == expected {
        ReconcileResult::Match
    } else {
        ReconcileResult::Mismatch
    })
}

/// Reconcile a V3 pool: read `slot0`+`liquidity` at `block` and compare.
pub async fn reconcile_v3<P: ChainProvider + ?Sized>(
    provider: &P,
    address: Address,
    expected: (U256, i32, U256), // (sqrt_price_x96, tick, liquidity)
    block: BlockId,
) -> Result<ReconcileResult> {
    let s0 = provider.call(address, slot0_calldata(), block).await?;
    let (sqrt, tick) = decode_slot0(&s0)?;
    let lq = provider.call(address, liquidity_calldata(), block).await?;
    let liquidity = decode_liquidity(&lq)?;
    Ok(if (sqrt, tick, liquidity) == expected {
        ReconcileResult::Match
    } else {
        ReconcileResult::Mismatch
    })
}

/// Reconcile one mirror pool against the chain **at the pool's own blockstamp** —
/// the same event-derived-==-`eth_call`-at-N equality the M4/M5 fixtures assert,
/// run live. On a [`Mismatch`](ReconcileResult::Mismatch) the pool is immediately
/// flipped `verified:false` in `mirror` (drift/missed-log/silent-reorg → stop
/// emitting it until the live path re-derives it). V4 pools (a `poolId`, no contract
/// address) are reconciled via StateView elsewhere and are skipped here (`Match`).
/// Returns the [`ReconcileResult`] so the caller can count/meter mismatches.
pub async fn reconcile_pool<P: ChainProvider + ?Sized>(
    provider: &P,
    mirror: &Mirror,
    pool: &Pool,
) -> Result<ReconcileResult> {
    let Some(addr) = pool.address.contract() else {
        return Ok(ReconcileResult::Match);
    };
    let block = BlockId::from(pool.blockstamp.number);
    let result = if let Some(v2) = &pool.v2 {
        reconcile_v2(provider, addr, (v2.reserve0.0, v2.reserve1.0), block).await?
    } else if let Some(v3) = &pool.v3 {
        reconcile_v3(
            provider,
            addr,
            (v3.sqrt_price_x96.0, v3.tick, v3.liquidity.0),
            block,
        )
        .await?
    } else {
        return Ok(ReconcileResult::Match);
    };
    if result == ReconcileResult::Mismatch {
        mirror.set_verified(&pool.address, false);
    }
    Ok(result)
}

/// Outcome counts of a batched reconcile round.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct ReconcileTally {
    /// Pools whose independent read matched the mirror (`verified` kept).
    pub matched: u64,
    /// Pools whose read differed — flipped `verified:false` and counted here.
    pub mismatched: u64,
    /// Pools whose read couldn't be completed (reverted/undecodable); `verified`
    /// left untouched, exactly as a failed single-pool read is non-fatal.
    pub failed: u64,
}

/// Cap on pools per reconcile multicall (a V3 pool is two sub-calls, so this stays
/// well under the [`l2i_rpc::prefetch::MULTICALL_CHUNK`] ceiling).
const RECONCILE_POOLS_PER_MULTICALL: usize = 200;

/// Decode + compare a V2 pool's `getReserves` blob to its mirror state. `None` on a
/// decode failure (treated as a non-fatal failed read, never a mismatch).
fn v2_matches(pool: &Pool, ret: &[u8]) -> Option<bool> {
    let v2 = pool.v2.as_ref()?;
    let got = decode_reserves(ret).ok()?;
    Some(got == (v2.reserve0.0, v2.reserve1.0))
}

/// Decode + compare a V3 pool's `slot0`+`liquidity` blobs to its mirror state.
fn v3_matches(pool: &Pool, slot0: &[u8], liquidity: &[u8]) -> Option<bool> {
    let v3 = pool.v3.as_ref()?;
    let (sqrt, tick) = decode_slot0(slot0).ok()?;
    let lq = decode_liquidity(liquidity).ok()?;
    Some((sqrt, tick, lq) == (v3.sqrt_price_x96.0, v3.tick, v3.liquidity.0))
}

/// Reconcile a whole batch of mirror pools with the chain in **one Multicall3 per
/// distinct block**, instead of one-to-two `eth_call`s per pool.
///
/// Reconciliation must read each pool at *its own* blockstamp (a newer block could
/// legitimately differ), so pools are grouped by block number and each group's
/// `getReserves` (V2) / `slot0`+`liquidity` (V3) reads are batched into a single
/// `aggregate3`. The equality proven is identical to [`reconcile_pool`]'s — a
/// mismatch flips the pool `verified:false` in `mirror` — but a 16-pool round that
/// used to cost up to ~32 sequential requests now costs one request per block
/// (typically one). V4/`poolId` pools are reconciled elsewhere and count as matched.
/// Never returns an error: a failed batch degrades to `failed` counts and leaves
/// `verified` untouched, so a flaky endpoint can't crash the ingestor.
pub async fn reconcile_batch<P: ChainProvider + ?Sized>(
    provider: &P,
    mirror: &Mirror,
    pools: &[Pool],
) -> ReconcileTally {
    let mut tally = ReconcileTally::default();

    // Group reconcilable pools by their stamped block; everything else "matches".
    let mut by_block: BTreeMap<u64, Vec<usize>> = BTreeMap::new();
    for (i, p) in pools.iter().enumerate() {
        if p.address.contract().is_none() || (p.v2.is_none() && p.v3.is_none()) {
            tally.matched += 1;
            continue;
        }
        by_block.entry(p.blockstamp.number).or_default().push(i);
    }

    for (number, idxs) in by_block {
        let block = BlockId::from(number);
        for chunk in idxs.chunks(RECONCILE_POOLS_PER_MULTICALL) {
            let mut calls: Vec<Call3> = Vec::with_capacity(chunk.len() * 2);
            // (pool index, is_v3): V2 consumes one result, V3 consumes two, in order.
            let mut plan: Vec<(usize, bool)> = Vec::with_capacity(chunk.len());
            for &i in chunk {
                let p = &pools[i];
                let addr = p
                    .address
                    .contract()
                    .expect("grouped pools are contract-address pools");
                if p.v2.is_some() {
                    calls.push(Call3::allow_failure(addr, get_reserves_calldata()));
                    plan.push((i, false));
                } else {
                    calls.push(Call3::allow_failure(addr, slot0_calldata()));
                    calls.push(Call3::allow_failure(addr, liquidity_calldata()));
                    plan.push((i, true));
                }
            }

            let results = match provider.multicall(calls, block).await {
                Ok(r) => r,
                Err(e) => {
                    tracing::debug!(error = %e, number, "reconcile multicall failed");
                    tally.failed += plan.len() as u64;
                    continue;
                }
            };

            let mut off = 0usize;
            for (i, is_v3) in plan {
                let p = &pools[i];
                let verdict = if is_v3 {
                    let s0 = results.get(off);
                    let lq = results.get(off + 1);
                    off += 2;
                    match (s0, lq) {
                        (Some(s0), Some(lq)) if s0.success && lq.success => {
                            v3_matches(p, &s0.returnData, &lq.returnData)
                        }
                        _ => None,
                    }
                } else {
                    let r = results.get(off);
                    off += 1;
                    match r {
                        Some(r) if r.success => v2_matches(p, &r.returnData),
                        _ => None,
                    }
                };
                match verdict {
                    Some(true) => tally.matched += 1,
                    Some(false) => {
                        mirror.set_verified(&p.address, false);
                        tally.mismatched += 1;
                        tracing::warn!(
                            chain_id = provider.chain_id(),
                            pool = %p.address,
                            "reconcile mismatch — pool marked verified:false"
                        );
                    }
                    None => tally.failed += 1,
                }
            }
        }
    }

    tally
}
