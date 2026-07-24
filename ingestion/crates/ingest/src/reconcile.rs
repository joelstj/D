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
use l2i_rpc::{BlockId, ChainProvider};

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
