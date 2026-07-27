//! One supervised per-chain ingestor: connect, gate the registry, seed the mirror,
//! then keep it live from `newHeads` (reorg) + `logs` (decode → mirror), with
//! periodic reconciliation and off-hot-path [`ChainContext`] refresh. This is the
//! actor of `docs/ARCHITECTURE.md §5`.
//!
//! The `select!` loop only does synchronous work (reorg classification, log decode →
//! mirror). The two RPC-heavy jobs — context refresh (gas/native price) and
//! reconciliation — run on their **own** spawned tasks so a slow RPC can never block
//! draining the `logs` subscription (which would drop live pool updates). Those tasks
//! are aborted when the loop returns, so each reconnect starts a clean set.

use crate::context::build_chain_context;
use alloy_primitives::{Address, B256};
use l2i_config::ChainConfig;
use l2i_core::{Blockstamp, ChainContext, PoolKind};
use l2i_ingest::event::{decode_sync_reserves, sync_topic};
use l2i_ingest::mirror::Mirror;
use l2i_ingest::reconcile::reconcile_batch;
use l2i_ingest::reorg::{BlockRef, ReorgOutcome, ReorgTracker};
use l2i_rpc::{BlockId, ChainProvider, Filter, RpcError};
use l2i_v4::apply_v4_swap;
use l2i_v4::event::{decode_v4_swap, v4_swap_topic};
use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::watch;

/// Extra blocks below a detected reorg's reported common ancestor to invalidate.
/// The true reorg depth is unknowable at first sight — a multi-block reorg reports
/// an optimistic (too-high) ancestor — so we widen the rollback by a small margin.
/// Over-invalidating only costs a re-verify; under-invalidating leaves stale state
/// flagged `verified:true` → phantom arbitrage (the dangerous direction).
const REORG_SAFETY_DEPTH: u64 = 2;

/// Verified pools reconciled per reconcile tick (a rotating window, to spread the
/// independent `eth_call` load rather than re-reading everything at once).
const RECONCILE_BATCH: usize = 16;

/// A spawned task that is aborted when this guard drops — ties a background worker's
/// lifetime to the connection that spawned it (so a reconnect doesn't leak the old
/// worker or double up).
struct AbortOnDrop(tokio::task::JoinHandle<()>);
impl Drop for AbortOnDrop {
    fn drop(&mut self) {
        self.0.abort();
    }
}

/// Static wiring for one chain's ingestor.
pub struct ChainIngestor<P: ChainProvider> {
    /// The chain id.
    pub chain_id: u64,
    /// The chain's config (gas params, native-price pools, hubs).
    pub cfg: ChainConfig,
    /// The provider (HTTP + optional WS).
    pub provider: Arc<P>,
    /// The shared in-memory mirror.
    pub mirror: Arc<Mirror>,
    /// Addresses to subscribe `logs` for (V2/V3 pools + the V4 PoolManager).
    pub log_addresses: Vec<Address>,
    /// V4 poolId → declared `PoolKey.fee` (the `0x800000` sentinel marks a dynamic-fee
    /// pool). Lets the live path apply a dynamic pool's effective per-swap fee via
    /// [`apply_v4_swap`] rather than discarding it. Empty on chains without V4.
    pub v4_declared_fees: HashMap<B256, u32>,
    /// The V4 `StateView` address, if this chain has V4 liquidity. Used to reconcile
    /// V4 (`poolId`) pools independently, just as V2/V3 pools are reconciled.
    pub state_view: Option<Address>,
    /// Reconcile cadence.
    pub reconcile_interval: Duration,
    /// Publishes the freshly-computed [`ChainContext`] to the aggregator (off the
    /// per-tick hot path).
    pub ctx_tx: watch::Sender<ChainContext>,
}

impl<P: ChainProvider + 'static> ChainIngestor<P> {
    /// Run the live loop until `shutdown` fires. Returns `Err` on a fatal transport
    /// failure **or when a subscription stream ends** (a dropped WS), so the
    /// supervisor can mark the chain unverified and reconnect with backoff.
    pub async fn run(&self, mut shutdown: watch::Receiver<bool>) -> l2i_rpc::Result<()> {
        let mut reorg = ReorgTracker::new(64);
        let mut heads = self.provider.subscribe_heads().await?;
        let filter = Filter::new().address(self.log_addresses.clone());
        let mut logs = self.provider.subscribe_logs(filter).await?;

        // Off-loop workers: gas/native-price refresh (~per block) and reconciliation.
        // Kept off the `select!` so their RPCs never stall log draining. Both are
        // aborted when this function returns (guards drop on every exit path).
        let block_period = Duration::from_millis(self.cfg.block_time_ms.clamp(200, 10_000));
        let _ctx_worker = AbortOnDrop(tokio::spawn(context_refresh_loop(
            self.cfg.clone(),
            self.provider.clone(),
            self.mirror.clone(),
            self.ctx_tx.clone(),
            block_period,
            shutdown.clone(),
        )));
        let _reconcile_worker = AbortOnDrop(tokio::spawn(reconcile_loop(
            self.chain_id,
            self.provider.clone(),
            self.mirror.clone(),
            self.state_view,
            self.reconcile_interval,
            shutdown.clone(),
        )));

        tracing::info!(chain_id = self.chain_id, "ingestor live");

        use futures::StreamExt;
        loop {
            tokio::select! {
                _ = shutdown.changed() => {
                    if *shutdown.borrow() {
                        tracing::info!(chain_id = self.chain_id, "ingestor shutting down");
                        return Ok(());
                    }
                }
                head = heads.next() => {
                    // A `None` means the subscription stream ended — the WS dropped.
                    // Surface it as an error so the supervisor restarts us; the old
                    // code left a disabled branch and lingered forever, feeding stale
                    // `verified:true` data.
                    let Some(head) = head else {
                        return Err(RpcError::Transport("newHeads subscription ended".into()));
                    };
                    self.on_head(&mut reorg, head);
                }
                log = logs.next() => {
                    let Some(log) = log else {
                        return Err(RpcError::Transport("logs subscription ended".into()));
                    };
                    self.apply_log(&log);
                }
            }
        }
    }

    /// Handle one new head **synchronously** (no RPC): reorg classification with a
    /// conservative safety margin, plus gap detection. Context refresh + reconcile are
    /// the off-loop workers' jobs, so this never blocks log draining.
    fn on_head(&self, reorg: &mut ReorgTracker, head: l2i_rpc::HeadSummary) {
        let outcome = reorg.observe(BlockRef {
            number: head.number,
            hash: head.hash,
            parent_hash: head.parent_hash,
        });
        match outcome {
            ReorgOutcome::Reorg { common_ancestor } => {
                // Widen the rollback below the (optimistic) reported ancestor so a
                // deeper multi-block reorg can't leave orphaned pools verified.
                let floor = common_ancestor.saturating_sub(REORG_SAFETY_DEPTH);
                let n = self.mirror.mark_unverified_after(floor);
                metrics_reorg(self.chain_id, n);
            }
            ReorgOutcome::Gap { expected, got } => {
                // Missed heads. Pool logs arrive on the *independent* logs
                // subscription, so this alone does not mean missed pool data; we flag
                // and meter it. (Note: the periodic reconcile checks decode drift at
                // each pool's own block, so it does NOT by itself recover a *missed*
                // forward log — that heals on the pool's next log or on reseed.)
                tracing::warn!(chain_id = self.chain_id, expected, got, "head gap detected");
                metrics::counter!(l2i_observability::names::HEAD_GAPS).increment(1);
            }
            ReorgOutcome::Extended | ReorgOutcome::Duplicate => {}
        }
    }

    /// Decode one log and update the mirror by pool type.
    fn apply_log(&self, log: &l2i_rpc::Log) {
        let Some(stamp) = blockstamp_from_log(self.chain_id, log) else {
            return;
        };
        let Some(topic0) = log.topics().first().copied() else {
            return;
        };
        // First hot-path stage: time the typed decode → mirror update. Records into
        // the DECODE_SECONDS histogram on drop (stack-only guard, no allocation).
        let _decode =
            l2i_observability::LatencyTimer::start(l2i_observability::names::DECODE_SECONDS);

        if topic0 == sync_topic() {
            if let Ok((r0, r1)) = decode_sync_reserves(&log.inner.data.data) {
                let id = l2i_core::PoolAddress::Contract(log.address());
                self.mirror.apply_v2_sync(&id, r0, r1, stamp);
            }
        } else if topic0 == l2i_ingest::event::v3_swap_topic() {
            if let Ok((sqrt, liq, tick)) =
                l2i_ingest::event::decode_v3_swap_data(&log.inner.data.data)
            {
                let id = l2i_core::PoolAddress::Contract(log.address());
                self.mirror.apply_v3_swap(&id, sqrt, tick, liq, stamp);
            }
        } else if topic0 == v4_swap_topic() {
            if let Ok(s) = decode_v4_swap(log) {
                // V4 pools live in the same mirror, keyed by poolId. Route through the
                // adapter so a dynamic-fee pool's effective fee (carried in the Swap
                // event) actually lands via set_fee_pips instead of being discarded.
                // `declared_fee` distinguishes a dynamic pool (0x800000) from a static
                // one; an unknown poolId defaults to 0 (non-dynamic) and apply_v4_swap
                // is then a no-op for it (the mirror holds no such pool).
                let declared_fee = self.v4_declared_fees.get(&s.pool_id).copied().unwrap_or(0);
                apply_v4_swap(&self.mirror, &s, declared_fee, stamp);
            }
        }
    }
}

/// Off-loop worker: recompute this chain's [`ChainContext`] (live gas + native
/// prices) ~once per block and publish it, so the aggregator reads a cached value
/// with no RPC on its per-tick path.
async fn context_refresh_loop<P: ChainProvider + ?Sized>(
    cfg: ChainConfig,
    provider: Arc<P>,
    mirror: Arc<Mirror>,
    ctx_tx: watch::Sender<ChainContext>,
    period: Duration,
    mut shutdown: watch::Receiver<bool>,
) {
    let mut interval = tokio::time::interval(period);
    interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
    loop {
        tokio::select! {
            _ = shutdown.changed() => { if *shutdown.borrow() { return; } }
            _ = interval.tick() => {
                let prev = ctx_tx.borrow().clone();
                let ctx = build_chain_context(&cfg, &*provider, &mirror, &prev).await;
                let _ = ctx_tx.send(ctx);
            }
        }
    }
}

/// Off-loop worker: reconcile a rotating window of verified pools against the chain
/// at each pool's blockstamp; a mismatch flips the pool `verified:false` (+ meter),
/// and the verified-pool gauge is refreshed each round.
async fn reconcile_loop<P: ChainProvider + ?Sized>(
    chain_id: u64,
    provider: Arc<P>,
    mirror: Arc<Mirror>,
    state_view: Option<Address>,
    period: Duration,
    mut shutdown: watch::Receiver<bool>,
) {
    let mut interval = tokio::time::interval(period);
    interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
    let mut cursor = 0usize;
    loop {
        tokio::select! {
            _ = shutdown.changed() => { if *shutdown.borrow() { return; } }
            _ = interval.tick() => {
                reconcile_round(&*provider, &mirror, state_view, &mut cursor).await;
                metrics::gauge!(
                    l2i_observability::names::VERIFIED_POOLS,
                    "chain_id" => chain_id.to_string()
                )
                .set(mirror.verified_count() as f64);
            }
        }
    }
}

/// Reconcile one rotating batch of verified pools. The continuous "our data is real"
/// proof — the M4/M5 equality (event-derived == `eth_call` at the pool's block) run
/// live; a mismatch means decode drift / a silent historical rewrite → `verified:false`.
async fn reconcile_round<P: ChainProvider + ?Sized>(
    provider: &P,
    mirror: &Mirror,
    state_view: Option<Address>,
    cursor: &mut usize,
) {
    let verified = mirror.snapshot_verified();
    if verified.is_empty() {
        return;
    }
    let start = *cursor % verified.len();
    let batch = verified.len().min(RECONCILE_BATCH);
    // The rotating window of pools to reconcile this round, batched into one
    // Multicall3 per distinct block (mismatches are flipped verified:false inside).
    let window: Vec<_> = (0..batch)
        .map(|i| verified[(start + i) % verified.len()].clone())
        .collect();
    let tally = reconcile_batch(provider, mirror, &window).await;
    let mut mismatched = tally.mismatched;
    // reconcile_batch skips V4 (poolId) pools; reconcile them independently via the
    // StateView so they get the same verified-honesty guarantee as V2/V3 — drift / a
    // missed Swap log flips them verified:false instead of being silently trusted.
    if let Some(sv) = state_view {
        let v4 = l2i_v4::reconcile_v4_batch(provider, mirror, sv, &window).await;
        mismatched += v4.mismatched;
    }
    if mismatched > 0 {
        metrics::counter!(l2i_observability::names::RECONCILE_MISMATCHES).increment(mismatched);
    }
    *cursor = start + batch;
}

fn blockstamp_from_log(chain_id: u64, log: &l2i_rpc::Log) -> Option<Blockstamp> {
    Some(Blockstamp {
        chain_id,
        number: log.block_number?,
        block_hash: log.block_hash?,
        timestamp: log.block_timestamp.unwrap_or(0),
    })
}

fn metrics_reorg(chain_id: u64, affected: usize) {
    tracing::warn!(
        chain_id,
        affected,
        "reorg handled — pools marked verified:false"
    );
    metrics::counter!(l2i_observability::names::REORGS_TOTAL).increment(1);
}

/// Seed a chain's V2/V3/V4 pools into `mirror` at the head block (used at boot and
/// after a reconnect). Returns the number of pools seeded.
pub async fn seed_all<P: ChainProvider + ?Sized>(
    provider: &P,
    mirror: &Mirror,
    pools: &[l2i_registry::gate::ValidatedPool],
    state_view: Option<Address>,
    blockstamp: Blockstamp,
    block: BlockId,
) -> anyhow::Result<usize> {
    let mut seeded = 0;
    for ps in l2i_ingest::v2::seed_v2_pools(provider, pools, blockstamp.clone(), block).await? {
        mirror.insert(ps);
        seeded += 1;
    }
    for ps in l2i_ingest::v3::seed_v3_pools(provider, pools, blockstamp.clone(), block).await? {
        mirror.insert(ps);
        seeded += 1;
    }
    if let Some(sv) = state_view {
        for ps in l2i_v4::seed_v4_pools(provider, pools, sv, blockstamp, block).await? {
            mirror.insert(ps);
            seeded += 1;
        }
    }
    let _ = PoolKind::V2; // keep the import meaningful across feature sets
    Ok(seeded)
}
