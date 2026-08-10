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
use l2i_ingest::event::{
    decode_sync_reserves, decode_v3_liquidity_change, sync_topic, v3_burn_topic, v3_mint_topic,
};
use l2i_ingest::mirror::Mirror;
use l2i_ingest::reconcile::reconcile_batch;
use l2i_ingest::reorg::{BlockRef, ReorgOutcome, ReorgTracker};
use l2i_rpc::{BlockId, ChainProvider, Filter, RpcError};
use l2i_v4::apply_v4_modify_liquidity;
use l2i_v4::apply_v4_swap;
use l2i_v4::event::{
    decode_v4_modify_liquidity, decode_v4_swap, v4_modify_liquidity_topic, v4_swap_topic,
};
use std::collections::HashMap;
use std::sync::Arc;
use std::time::{Duration, Instant};
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

/// How often the liveness watchdog checks for a stalled `heads` subscription.
const WATCHDOG_TICK: Duration = Duration::from_secs(5);

/// Floor under [`stale_after`]'s multiplier, so a fast chain (e.g. Arbitrum's
/// ~250ms blocks) isn't declared stalled after a few seconds of ordinary jitter.
const STALE_HEAD_FLOOR: Duration = Duration::from_secs(30);

/// How many block periods of complete silence on `heads` (zero new blocks, not
/// just zero trading activity) before treating the subscription as stalled
/// rather than healthy-but-quiet.
const STALE_HEAD_MULTIPLIER: u32 = 20;

/// How long a chain's `heads` subscription may go without a new block before its
/// silence is treated as a stall rather than a healthy quiet period — the
/// liveness watchdog for the CRITICAL, previously-recorded gap: an upstream
/// node's WS can go quiet **without ever erroring** (distinct from a clean
/// disconnect/dropped-stream, which the loop already handles), so the old
/// `select!` had no branch that could ever notice — nothing timed out, the
/// supervisor never saw an `Err`, and `mark_all_unverified()`/reconnect never
/// fired. A silently-stalled subscription then left the mirror `verified:true`
/// forever, unbounded, violating the `verified` honesty invariant.
///
/// Deliberately gated on **`heads` alone**, not `logs`: every configured chain
/// produces a new block on a roughly fixed cadence regardless of trading
/// activity, so "no head for a long time" is a reliable staleness signal. Logs
/// are event-driven and can be legitimately silent for a long time on a quiet
/// pool — gating on log silence too would false-positive on a perfectly healthy,
/// just-quiet chain.
///
/// A pure function so the threshold logic is unit-testable without a live
/// socket. Chosen to be conservative by design: the two failure directions are
/// not symmetric. A false positive costs one extra reconnect, which
/// self-heals via the supervisor's existing, already-tested backoff/reseed
/// path (`pipeline.rs::supervise_chain`) — a bounded, cheap, self-correcting
/// cost. A false negative (threshold too loose) leaves the exact unbounded
/// staleness this function exists to close. `block_time_ms` is a per-chain
/// config hint, not a guarantee (`config/config.example.toml`), so it is
/// clamped before use — the same clamp `context_refresh_loop`'s period already
/// applies.
fn stale_after(block_time_ms: u64) -> Duration {
    STALE_HEAD_FLOOR
        .max(Duration::from_millis(block_time_ms.clamp(200, 10_000)) * STALE_HEAD_MULTIPLIER)
}

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
    /// failure, **when a subscription stream ends** (a dropped WS), or **when
    /// `heads` has gone silent for too long** (a stalled-but-not-errored WS — see
    /// [`stale_after`]), so the supervisor can mark the chain unverified and
    /// reconnect with backoff.
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

        // Liveness watchdog (see `stale_after`): the clock starts now, right as
        // the subscription is freshly established, so a WS that connects but
        // never pushes a single head is caught too, not just one that goes
        // quiet mid-session.
        let stale_after = stale_after(self.cfg.block_time_ms);
        let mut last_head_at = Instant::now();
        let mut watchdog = tokio::time::interval(WATCHDOG_TICK);
        watchdog.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);

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
                    last_head_at = Instant::now();
                    self.on_head(&mut reorg, head);
                }
                log = logs.next() => {
                    let Some(log) = log else {
                        return Err(RpcError::Transport("logs subscription ended".into()));
                    };
                    apply_log(self.chain_id, &self.mirror, &self.v4_declared_fees, &log);
                }
                _ = watchdog.tick() => {
                    let idle = last_head_at.elapsed();
                    if idle > stale_after {
                        // The socket never errored — it just went quiet. Nothing
                        // else in this loop would ever notice on its own, so this
                        // is the only path that can end the silence: return `Err`
                        // to reuse the exact same, already-tested recovery the
                        // "stream ended" branches above use (the supervisor marks
                        // the mirror unverified and reconnects with backoff).
                        tracing::warn!(
                            chain_id = self.chain_id,
                            idle_secs = idle.as_secs(),
                            stale_after_secs = stale_after.as_secs(),
                            "no new head for too long — treating WS as stalled"
                        );
                        return Err(RpcError::Timeout(idle));
                    }
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
}

/// Decode one log and update the mirror by pool type. A free function (rather than
/// a `&self` method) because it only ever needs these three fields — never the
/// provider, config, or anything else on [`ChainIngestor`] — which keeps it
/// directly unit-testable without fabricating the rest of the struct.
fn apply_log(
    chain_id: u64,
    mirror: &Mirror,
    v4_declared_fees: &HashMap<B256, u32>,
    log: &l2i_rpc::Log,
) {
    let Some(stamp) = blockstamp_from_log(chain_id, log) else {
        return;
    };
    let Some(topic0) = log.topics().first().copied() else {
        return;
    };
    // First hot-path stage: time the typed decode → mirror update. Records into
    // the DECODE_SECONDS histogram on drop (stack-only guard, no allocation).
    let _decode = l2i_observability::LatencyTimer::start(l2i_observability::names::DECODE_SECONDS);

    if topic0 == sync_topic() {
        if let Ok((r0, r1)) = decode_sync_reserves(&log.inner.data.data) {
            let id = l2i_core::PoolAddress::Contract(log.address());
            mirror.apply_v2_sync(&id, r0, r1, stamp);
        }
    } else if topic0 == l2i_ingest::event::v3_swap_topic() {
        if let Ok((sqrt, liq, tick)) = l2i_ingest::event::decode_v3_swap_data(&log.inner.data.data)
        {
            let id = l2i_core::PoolAddress::Contract(log.address());
            mirror.apply_v3_swap(&id, sqrt, tick, liq, stamp);
        }
    } else if topic0 == v4_swap_topic() {
        if let Ok(s) = decode_v4_swap(log) {
            // V4 pools live in the same mirror, keyed by poolId. Route through the
            // adapter so a dynamic-fee pool's effective fee (carried in the Swap
            // event) actually lands via set_fee_pips instead of being discarded.
            // `declared_fee` distinguishes a dynamic pool (0x800000) from a static
            // one; an unknown poolId defaults to 0 (non-dynamic) and apply_v4_swap
            // is then a no-op for it (the mirror holds no such pool).
            let declared_fee = v4_declared_fees.get(&s.pool_id).copied().unwrap_or(0);
            apply_v4_swap(mirror, &s, declared_fee, stamp);
        }
    } else if topic0 == v3_mint_topic() || topic0 == v3_burn_topic() {
        // In-range Mint/Burn changes the pool's *active* liquidity (only when
        // the modified range brackets the current tick — apply_v3_liquidity_change
        // itself checks that). Without this, the mirror's `liquidity` figure
        // silently drifts from chain truth between Swaps on the same pool, while
        // `re_stamp` keeps re-labelling it `verified:true` at the live head every
        // tick regardless (see docs/ARCHITECTURE.md §6 "never lie").
        if let Ok(change) = decode_v3_liquidity_change(log) {
            let id = l2i_core::PoolAddress::Contract(log.address());
            mirror.apply_v3_liquidity_change(
                &id,
                change.tick_lower,
                change.tick_upper,
                change.amount,
                change.add,
                stamp,
            );
        }
    } else if topic0 == v4_modify_liquidity_topic() {
        // V4 analogue of the V3 Mint/Burn branch above — same staleness risk,
        // same fix shape.
        if let Ok(change) = decode_v4_modify_liquidity(log) {
            apply_v4_modify_liquidity(mirror, &change, stamp);
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

#[cfg(test)]
mod apply_log_tests {
    use super::*;
    use alloy_primitives::{Bytes, I256, U256};
    use l2i_core::Token;
    use l2i_ingest::mirror::{LiveState, PoolState};

    /// Encode a non-negative `int24` (sign-extended into an i32) as the 32-byte
    /// big-endian word `int24_from_word` expects — mirrors how a real V3/V4 log
    /// packs an indexed/data `int24` tick.
    fn word_i32(v: i32) -> B256 {
        assert!(v >= 0, "test helper only covers non-negative ticks");
        let mut w = [0u8; 32];
        w[28..32].copy_from_slice(&v.to_be_bytes());
        B256::from(w)
    }

    fn test_log(address: Address, topics: Vec<B256>, data: Vec<u8>) -> l2i_rpc::Log {
        l2i_rpc::Log {
            inner: alloy_primitives::Log::new(address, topics, Bytes::from(data)).unwrap(),
            block_hash: Some(B256::from([7u8; 32])),
            block_number: Some(100),
            block_timestamp: Some(1_700_000_000),
            ..Default::default()
        }
    }

    fn v3_pool_state(identity: l2i_core::PoolAddress, tick: i32, liquidity: u64) -> PoolState {
        PoolState {
            identity,
            kind: PoolKind::V3,
            fee_pips: 3000,
            token0: Token::with_symbol(1, Address::from([1u8; 20]), 18, "A"),
            token1: Token::with_symbol(1, Address::from([2u8; 20]), 6, "B"),
            state: LiveState::V3 {
                sqrt_price_x96: U256::from(1u64) << 96,
                tick,
                liquidity: U256::from(liquidity),
            },
            blockstamp: Blockstamp {
                chain_id: 1,
                number: 99,
                block_hash: B256::from([6u8; 32]),
                timestamp: 1,
            },
            verified: true,
        }
    }

    fn active_liquidity(mirror: &Mirror, id: &l2i_core::PoolAddress) -> U256 {
        match mirror
            .get(id)
            .expect("pool must still be in the mirror")
            .state
        {
            LiveState::V3 { liquidity, .. } => liquidity,
            LiveState::V2 { .. } => panic!("expected V3-shaped state"),
        }
    }

    // Regression coverage for the CRITICAL finding: V3 Mint/Burn and V4
    // ModifyLiquidity were fully decoded, fully applied to the mirror, and
    // directly unit-tested at the decode/mirror layer — but `apply_log` (the
    // *only* place a live topic0 is ever dispatched) had no branch for either,
    // so both were silently dropped on the live path while `re_stamp` kept
    // re-labelling the resulting stale liquidity `verified:true` every tick.

    #[test]
    fn v3_mint_in_range_grows_active_liquidity() {
        let mirror = Mirror::new();
        let pool_addr = Address::from([9u8; 20]);
        let id = l2i_core::PoolAddress::Contract(pool_addr);
        mirror.insert(v3_pool_state(id, 100, 1000));

        // event Mint(address sender, address indexed owner, int24 indexed
        // tickLower, int24 indexed tickUpper, uint128 amount, uint256 amount0,
        // uint256 amount1) — range [0, 200) brackets tick=100; amount=500 lives
        // in data word 1 (data: [sender, amount, amount0, amount1]).
        let mut data = vec![0u8; 128];
        data[32..64].copy_from_slice(&U256::from(500u64).to_be_bytes::<32>());
        let log = test_log(
            pool_addr,
            vec![
                l2i_ingest::event::v3_mint_topic(),
                B256::ZERO, // owner (unused by decode)
                word_i32(0),
                word_i32(200),
            ],
            data,
        );

        apply_log(1, &mirror, &HashMap::new(), &log);

        assert_eq!(active_liquidity(&mirror, &id), U256::from(1500u64));
    }

    #[test]
    fn v3_burn_in_range_shrinks_active_liquidity() {
        let mirror = Mirror::new();
        let pool_addr = Address::from([9u8; 20]);
        let id = l2i_core::PoolAddress::Contract(pool_addr);
        mirror.insert(v3_pool_state(id, 100, 1000));

        // event Burn(address indexed owner, int24 indexed tickLower, int24
        // indexed tickUpper, uint128 amount, uint256 amount0, uint256 amount1) —
        // amount is data word 0.
        let mut data = vec![0u8; 96];
        data[0..32].copy_from_slice(&U256::from(300u64).to_be_bytes::<32>());
        let log = test_log(
            pool_addr,
            vec![
                l2i_ingest::event::v3_burn_topic(),
                B256::ZERO,
                word_i32(0),
                word_i32(200),
            ],
            data,
        );

        apply_log(1, &mirror, &HashMap::new(), &log);

        assert_eq!(active_liquidity(&mirror, &id), U256::from(700u64));
    }

    #[test]
    fn v3_mint_outside_the_current_tick_range_does_not_touch_active_liquidity() {
        let mirror = Mirror::new();
        let pool_addr = Address::from([9u8; 20]);
        let id = l2i_core::PoolAddress::Contract(pool_addr);
        mirror.insert(v3_pool_state(id, 100, 1000));

        // Range [200, 300) does not bracket tick=100 — must be a no-op.
        let mut data = vec![0u8; 128];
        data[32..64].copy_from_slice(&U256::from(500u64).to_be_bytes::<32>());
        let log = test_log(
            pool_addr,
            vec![
                l2i_ingest::event::v3_mint_topic(),
                B256::ZERO,
                word_i32(200),
                word_i32(300),
            ],
            data,
        );

        apply_log(1, &mirror, &HashMap::new(), &log);

        assert_eq!(active_liquidity(&mirror, &id), U256::from(1000u64));
    }

    #[test]
    fn v4_modify_liquidity_dispatches_to_the_mirror_by_pool_id() {
        let mirror = Mirror::new();
        let pool_id = B256::from([5u8; 32]);
        let id = l2i_core::PoolAddress::PoolId(pool_id);
        mirror.insert(v3_pool_state(id, 100, 1000));
        // The PoolManager singleton — irrelevant to the lookup (V4 identity comes
        // from the poolId topic, not the emitting address), included for realism.
        let pool_manager = Address::from([0x99u8; 20]);

        // event ModifyLiquidity(bytes32 indexed id, address indexed sender, int24
        // tickLower, int24 tickUpper, int256 liquidityDelta, bytes32 salt) — data:
        // [tickLower, tickUpper, liquidityDelta, salt]; delta=+500 brackets tick=100.
        let mut data = vec![0u8; 128];
        data[0..32].copy_from_slice(word_i32(0).as_slice());
        data[32..64].copy_from_slice(word_i32(200).as_slice());
        data[64..96].copy_from_slice(&I256::try_from(500i64).unwrap().to_be_bytes::<32>());
        let log = test_log(
            pool_manager,
            vec![
                l2i_v4::event::v4_modify_liquidity_topic(),
                pool_id,
                B256::ZERO,
            ],
            data,
        );

        apply_log(1, &mirror, &HashMap::new(), &log);

        assert_eq!(active_liquidity(&mirror, &id), U256::from(1500u64));
    }

    #[test]
    fn v3_swap_and_sync_dispatch_still_work_after_the_refactor_to_a_free_function() {
        // Cheap regression guard: the CRITICAL-finding fix turned `apply_log`
        // from a `&self` method into a free function — prove the pre-existing
        // V2 Sync and V3 Swap branches still fire correctly through it.
        let mirror = Mirror::new();
        let pool_addr = Address::from([3u8; 20]);
        let id = l2i_core::PoolAddress::Contract(pool_addr);
        mirror.insert(PoolState {
            identity: id,
            kind: PoolKind::V2,
            fee_pips: 3000,
            token0: Token::with_symbol(1, Address::from([1u8; 20]), 18, "A"),
            token1: Token::with_symbol(1, Address::from([2u8; 20]), 6, "B"),
            state: LiveState::V2 {
                reserve0: U256::from(1u64),
                reserve1: U256::from(1u64),
            },
            blockstamp: Blockstamp {
                chain_id: 1,
                number: 99,
                block_hash: B256::from([6u8; 32]),
                timestamp: 1,
            },
            verified: true,
        });

        let mut data = vec![0u8; 64];
        data[0..32].copy_from_slice(&U256::from(111u64).to_be_bytes::<32>());
        data[32..64].copy_from_slice(&U256::from(222u64).to_be_bytes::<32>());
        let log = test_log(pool_addr, vec![sync_topic()], data);

        apply_log(1, &mirror, &HashMap::new(), &log);

        match mirror.get(&id).unwrap().state {
            LiveState::V2 { reserve0, reserve1 } => {
                assert_eq!(reserve0, U256::from(111u64));
                assert_eq!(reserve1, U256::from(222u64));
            }
            LiveState::V3 { .. } => panic!("expected V2 state"),
        }
    }
}

#[cfg(test)]
mod watchdog_tests {
    use super::*;

    // Regression coverage for the CRITICAL finding: a chain's WS `heads`
    // subscription could go silent without ever erroring, and nothing in the
    // old `select!` loop could ever notice — no timer branch existed at all,
    // so the supervisor's reconnect/mark-unverified path never fired.
    // `stale_after` is the pure threshold decision this watchdog runs on;
    // exercised here without a live socket.

    #[test]
    fn fast_chain_is_governed_by_the_floor_not_the_multiplier() {
        // Arbitrum-like: 250ms blocks. 20x that is only 5s — far too
        // aggressive for real-world jitter — so the 30s floor must dominate.
        assert_eq!(stale_after(250), STALE_HEAD_FLOOR);
    }

    #[test]
    fn slower_chain_scales_with_the_multiplier_above_the_floor() {
        // OP-Stack-like: 2000ms blocks. 20x = 40s, above the 30s floor.
        assert_eq!(stale_after(2_000), Duration::from_secs(40));
    }

    #[test]
    fn boundary_block_time_lands_exactly_on_the_floor() {
        // 1500ms * 20 = 30_000ms, exactly the floor — proves `.max` doesn't
        // accidentally pick the smaller side at the crossover.
        assert_eq!(stale_after(1_500), STALE_HEAD_FLOOR);
    }

    #[test]
    fn extreme_config_is_clamped_before_use() {
        // Mirrors the same clamp `context_refresh_loop`'s block period
        // already applies, so a misconfigured 0 or a huge value can't produce
        // a nonsensical (too-tight or too-loose) threshold.
        assert_eq!(stale_after(0), STALE_HEAD_FLOOR); // clamped up to 200ms -> floor wins
        assert_eq!(
            stale_after(1_000_000),
            Duration::from_millis(10_000) * STALE_HEAD_MULTIPLIER, // clamped down to 10s
        );
    }

    #[test]
    fn threshold_is_never_shorter_than_the_floor_across_realistic_configs() {
        // No real `block_time_ms` in `config/config.example.toml` (250, 1000,
        // 2000) can ever produce a threshold an operator would consider
        // trigger-happy.
        for block_time_ms in [250, 1_000, 2_000] {
            assert!(stale_after(block_time_ms) >= STALE_HEAD_FLOOR);
        }
    }
}
