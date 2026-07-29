//! Wiring: config → observability → engine → output → **supervised** per-chain
//! ingestors → aggregator loop. Every chain runs under a reconnect/backoff
//! supervisor that marks the mirror unverified on a drop (no stale emission) and
//! reseeds on reconnect; the aggregator sends on the configured **cadence** (not a
//! fixed poll), reads each chain's context from a **cached** channel (no per-tick
//! RPC), and warm-starts from a persisted snapshot. Graceful shutdown + `SIGHUP`.

use crate::context::{build_chain_context, initial_context};
use crate::crosschain::build_cross_chain;
use crate::ingestor::{seed_all, ChainIngestor};
use alloy_primitives::{Address, B256};
use l2i_aggregator::{
    build_detect_request, re_stamp, Cadence, ChainSnapshot, IncrementalPolicy, IncrementalTracker,
    RequestConfig,
};
use l2i_config::{CacheConfig, ChainConfig, Config};
use l2i_core::{Blockstamp, ChainContext};
use l2i_engine_client::{
    retain_valid, EngineClient, HttpConfig, HttpEngineClient, SubprocessEngineClient,
};
use l2i_ingest::mirror::Mirror;
use l2i_observability::{names, LatencyTimer};
use l2i_output::{Envelope, Latency, OutputSink, Stage};
use l2i_registry::{gate::GatePolicy, load_registry_file, validate_registry};
use l2i_rpc::backoff::{Backoff, BackoffPolicy};
use l2i_rpc::{AlloyProvider, BlockId, ChainProvider};
use std::collections::{BTreeMap, HashMap};
use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant, SystemTime};
use tokio::sync::watch;

/// A supervised chain the aggregator reads from. The mirror + context channel are
/// shared with the background supervisor task.
struct ChainHandle {
    cfg: ChainConfig,
    mirror: Arc<Mirror>,
    ctx_rx: watch::Receiver<ChainContext>,
    /// Bumped by the supervisor on each (re)seed. A change means a fresh engine
    /// session, so the aggregator resets its incremental tracker/policy.
    generation: Arc<AtomicU64>,
}

/// Run the whole component until a shutdown signal.
pub async fn run(config: Config) -> anyhow::Result<()> {
    // Observability: install the recorder, serve /health + /metrics on metrics_bind
    // (kept for compatibility with anything already scraping /health there) *and*
    // /health alone on its own configured health_bind — previously parsed and
    // printed by --check-config but never actually bound to a listener.
    if let Ok(handle) = l2i_observability::install_metrics() {
        let router = l2i_observability::router(handle);
        if let Err(e) = l2i_observability::serve(&config.observability.metrics_bind, router).await {
            tracing::warn!(error = %e, "metrics server failed to bind");
        }
    }
    if config.observability.health_bind != config.observability.metrics_bind {
        let health_router = l2i_observability::health_router();
        if let Err(e) =
            l2i_observability::serve(&config.observability.health_bind, health_router).await
        {
            tracing::warn!(error = %e, "health server failed to bind");
        }
    }

    // Engine + output.
    let engine = build_engine(&config)?;
    match engine.health().await {
        Ok(true) => tracing::info!("engine healthy"),
        _ => tracing::warn!("engine not healthy yet — will keep trying on the hot path"),
    }
    let sink = l2i_output::sink_from_config(
        &config.output.sink,
        config.output.ws_bind.as_deref().unwrap_or("0.0.0.0:9001"),
    )
    .await?;

    let (shutdown_tx, shutdown_rx) = watch::channel(false);

    // Supervise each enabled chain (connect + seed + run, reconnecting with backoff).
    let mut handles: Vec<ChainHandle> = Vec::new();
    for chain in config.enabled_chains().cloned().collect::<Vec<_>>() {
        handles.push(spawn_chain(
            chain,
            config.cache.clone(),
            shutdown_rx.clone(),
        ));
    }
    metrics::gauge!(names::CHAINS_LIVE).set(handles.len() as f64);
    tracing::info!(chains = handles.len(), "chains supervised");

    let agg = tokio::spawn(aggregator_loop(
        config,
        handles,
        engine,
        sink,
        shutdown_rx.clone(),
    ));

    wait_for_signals(shutdown_tx).await;
    let _ = agg.await;
    tracing::info!("shutdown complete");
    Ok(())
}

fn build_engine(config: &Config) -> anyhow::Result<Box<dyn EngineClient>> {
    let timeout = Duration::from_millis(config.engine.call_timeout_ms);
    match config.engine.transport.as_str() {
        "subprocess" => {
            let cmd = config
                .engine
                .subprocess_cmd
                .clone()
                .unwrap_or_else(|| "python -m l2arb.api.runner".into());
            Ok(Box::new(SubprocessEngineClient::new(&cmd, timeout)))
        }
        _ => Ok(Box::new(HttpEngineClient::new(HttpConfig {
            base_url: config.engine.http_url.clone(),
            detect_path: config.engine.detect_path.clone(),
            health_path: config.engine.health_path.clone(),
            timeout,
        })?)),
    }
}

/// Create a chain's shared state and spawn its background supervisor. Returns
/// immediately — connect/seed happen (and retry) inside the supervisor, so a chain
/// whose node is briefly down at boot keeps trying instead of being abandoned.
fn spawn_chain(
    cfg: ChainConfig,
    cache: CacheConfig,
    shutdown_rx: watch::Receiver<bool>,
) -> ChainHandle {
    let mirror = Arc::new(Mirror::new());
    let generation = Arc::new(AtomicU64::new(0));
    let (ctx_tx, ctx_rx) = watch::channel(initial_context(&cfg));

    tokio::spawn(supervise_chain(
        cfg.clone(),
        cache,
        mirror.clone(),
        ctx_tx,
        generation.clone(),
        shutdown_rx,
    ));

    ChainHandle {
        cfg,
        mirror,
        ctx_rx,
        generation,
    }
}

/// The per-chain supervisor: warm-start restore, then a connect→seed→run loop that
/// reconnects with exponential backoff and, on every exit, marks the mirror
/// unverified so stale data stops flowing. Flushes a final snapshot on shutdown.
async fn supervise_chain(
    cfg: ChainConfig,
    cache: CacheConfig,
    mirror: Arc<Mirror>,
    ctx_tx: watch::Sender<ChainContext>,
    generation: Arc<AtomicU64>,
    mut shutdown_rx: watch::Receiver<bool>,
) {
    // Warm start: restore the last snapshot (as verified:false) if fresh, so we serve
    // a last-known picture immediately instead of a cold RPC storm.
    if cache.enabled {
        match l2i_ingest::load_snapshot(Path::new(&cache.dir), cfg.chain_id) {
            Ok(Some(snap)) if snap.is_fresh(now_unix(), cache.max_staleness_secs) => {
                let n = mirror.restore(snap.pools);
                tracing::info!(chain = %cfg.name, restored = n, "warm-start: restored cached pools (verified:false)");
            }
            Ok(_) => {}
            Err(e) => {
                tracing::warn!(chain = %cfg.name, error = %e, "warm-start snapshot unreadable")
            }
        }
        tokio::spawn(snapshot_task(
            cache.clone(),
            mirror.clone(),
            cfg.chain_id,
            shutdown_rx.clone(),
        ));
    }

    let mut backoff = Backoff::with_seed(BackoffPolicy::default(), cfg.chain_id);
    loop {
        if *shutdown_rx.borrow() {
            break;
        }
        match connect_seed_run(&cfg, &mirror, &ctx_tx, &generation, shutdown_rx.clone()).await {
            Ok(()) => break, // clean shutdown
            Err(e) => {
                tracing::warn!(chain = %cfg.name, error = %e, "chain ingestor exited — reconnecting");
                // Stop stale emission: nothing is trustworthy until we re-derive.
                mirror.mark_all_unverified();
                metrics::counter!(names::INGESTOR_RECONNECTS).increment(1);
                let delay = backoff.next_delay();
                tokio::select! {
                    _ = tokio::time::sleep(delay) => {}
                    _ = shutdown_rx.changed() => {}
                }
            }
        }
    }

    if cache.enabled {
        let _ = write_snapshot_now(&cache, &mirror, cfg.chain_id);
    }
}

/// One connect → gate → seed → run cycle. Returns `Ok(())` only on a clean shutdown;
/// any transport/seed failure or subscription drop is an `Err` the supervisor retries.
async fn connect_seed_run(
    cfg: &ChainConfig,
    mirror: &Arc<Mirror>,
    ctx_tx: &watch::Sender<ChainContext>,
    generation: &Arc<AtomicU64>,
    shutdown_rx: watch::Receiver<bool>,
) -> anyhow::Result<()> {
    let provider =
        Arc::new(AlloyProvider::connect(cfg.chain_id, &cfg.http_url, Some(&cfg.ws_url)).await?);

    // Gate the registry on-chain, then seed the mirror at head.
    let registry = load_registry_file(&cfg.pool_registry)?;
    let head = provider.head(BlockId::latest()).await?;
    let at = BlockId::from(head.number);
    let blockstamp = Blockstamp {
        chain_id: cfg.chain_id,
        number: head.number,
        block_hash: head.hash,
        timestamp: head.timestamp,
    };
    let policy = GatePolicy {
        safe_hooks: cfg
            .safe_hooks
            .iter()
            .filter_map(|s| s.parse().ok())
            .collect(),
        check_factory: true,
        ..Default::default()
    };
    let outcome = validate_registry(&*provider, &registry, at, &policy).await;
    tracing::info!(
        chain = %cfg.name,
        accepted = outcome.accepted.len(),
        rejected = outcome.rejected.len(),
        "validation gate complete"
    );

    // Prime the context channel with a REAL gas reading *before* seeding makes any
    // pool visible, so the aggregator can never emit a freshly-verified pool against
    // the `gas_price = 0` sentinel of `initial_context` (which would under-cost gas →
    // phantom profit). Native prices are empty here (no pools yet); they fill in on
    // the post-seed refresh below and the per-block context worker.
    let prev = ctx_tx.borrow().clone();
    let ctx = build_chain_context(cfg, &*provider, mirror, &prev).await;
    let _ = ctx_tx.send(ctx);

    let state_view = cfg
        .uniswap_v4_state_view
        .as_ref()
        .and_then(|s| s.parse::<Address>().ok());
    let seeded = seed_all(
        &*provider,
        mirror,
        &outcome.accepted,
        state_view,
        blockstamp,
        at,
    )
    .await?;
    tracing::info!(chain = %cfg.name, seeded, "mirror seeded");
    // A fresh seed = a fresh engine session: signal the aggregator to send a full
    // (incremental:false) request next.
    generation.fetch_add(1, Ordering::Relaxed);

    // Subscribe addresses: V2/V3 pool contracts + the V4 PoolManager (if any).
    let mut log_addresses: Vec<Address> = outcome
        .accepted
        .iter()
        .filter_map(|p| p.entry.identity().contract())
        .collect();
    if let Some(pm) = cfg
        .uniswap_v4_pool_manager
        .as_ref()
        .and_then(|s| s.parse::<Address>().ok())
    {
        log_addresses.push(pm);
    }

    // Post-seed refresh: now the WETH/T pools exist, so native prices derive too.
    let prev = ctx_tx.borrow().clone();
    let ctx = build_chain_context(cfg, &*provider, mirror, &prev).await;
    let _ = ctx_tx.send(ctx);

    // poolId → declared PoolKey.fee (0x800000 = dynamic), so the live path applies a
    // dynamic pool's effective per-swap fee via apply_v4_swap instead of discarding it.
    let v4_declared_fees: HashMap<B256, u32> = outcome
        .accepted
        .iter()
        .filter_map(|p| p.entry.identity().pool_id().map(|id| (id, p.fee_pips)))
        .collect();

    let ingestor = ChainIngestor {
        chain_id: cfg.chain_id,
        cfg: cfg.clone(),
        provider,
        mirror: mirror.clone(),
        log_addresses,
        v4_declared_fees,
        state_view,
        reconcile_interval: Duration::from_millis(cfg.reconcile_interval_ms.max(1)),
        ctx_tx: ctx_tx.clone(),
    };
    ingestor.run(shutdown_rx).await?;
    Ok(())
}

/// Periodically flush the verified mirror to disk (warm-start cache), plus a final
/// flush when shutdown fires.
async fn snapshot_task(
    cache: CacheConfig,
    mirror: Arc<Mirror>,
    chain_id: u64,
    mut shutdown: watch::Receiver<bool>,
) {
    let mut interval =
        tokio::time::interval(Duration::from_millis(cache.snapshot_interval_ms.max(1)));
    interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
    loop {
        tokio::select! {
            _ = shutdown.changed() => { if *shutdown.borrow() { break; } }
            _ = interval.tick() => { let _ = write_snapshot_now(&cache, &mirror, chain_id); }
        }
    }
    let _ = write_snapshot_now(&cache, &mirror, chain_id);
}

fn write_snapshot_now(cache: &CacheConfig, mirror: &Mirror, chain_id: u64) -> std::io::Result<()> {
    let verified = mirror.snapshot_verified();
    let head = verified
        .iter()
        .map(|p| &p.blockstamp)
        .max_by_key(|b| b.number)
        .cloned()
        .unwrap_or(Blockstamp {
            chain_id,
            number: 0,
            block_hash: alloy_primitives::B256::ZERO,
            timestamp: 0,
        });
    l2i_ingest::write_snapshot(
        Path::new(&cache.dir),
        chain_id,
        &verified,
        &head,
        now_unix(),
    )
}

fn now_unix() -> u64 {
    SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// Wall-clock milliseconds since the Unix epoch — the single-host end-to-end
/// latency anchor stamped into each opportunities envelope. Unlike [`Instant`] this
/// is a *wall* clock (comparable across processes on one host), not a monotonic
/// duration; the dashboard measures `now_ms - origin_wall_ms` against it.
fn now_wall_ms() -> u64 {
    SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

/// Whether the aggregator tick should hold `chain_id` back this round rather than
/// cost verified pools against a gas/fee reading that looks fabricated. Returns a
/// human-readable reason, or `None` to proceed.
///
/// A `0` execution gas price is never real on any of the five chains — it is
/// `context::initial_context`'s pre-first-read sentinel. Costing a verified pool
/// against it would under-cost gas into phantom profit (prime directive 1), so the
/// chain is held back until a real reading lands.
///
/// The same risk applies to the L1 data-availability fee, but **only** on the four
/// OP-Stack chains (Base/Optimism/Unichain/Ink): there, `0` is likewise the
/// pre-first-read sentinel for `GasPriceOracle.getL1Fee`. Arbitrum's
/// `l1_data_fee_wei` is *legitimately* always `0` (its L1 cost is folded into gas
/// units — `l2i_chains::GasModel::Arbitrum`), so gating on it there would hold
/// Arbitrum back forever; an unrecognised chain id conservatively behaves like
/// Arbitrum (not held back on this check alone) since it cannot be an OP-Stack
/// chain in the compiled-in registry.
fn hold_back_reason(chain_id: u64, ctx: &ChainContext) -> Option<&'static str> {
    if ctx.gas_price_wei == 0 {
        return Some("no real gas price yet (won't cost pools against a 0)");
    }
    let is_op_stack = l2i_chains::by_id(chain_id)
        .map(|s| s.gas_model == l2i_chains::GasModel::OpStack)
        .unwrap_or(false);
    if is_op_stack && ctx.l1_data_fee_wei == 0 {
        return Some("no real L1 data fee yet (won't cost pools against a 0)");
    }
    None
}

#[allow(clippy::too_many_lines)]
async fn aggregator_loop(
    config: Config,
    handles: Vec<ChainHandle>,
    engine: Box<dyn EngineClient>,
    sink: Box<dyn OutputSink>,
    mut shutdown_rx: watch::Receiver<bool>,
) {
    let cadence = Cadence {
        mode: config.cadence.mode,
        min_interval_ms: config.cadence.min_interval_ms,
        max_interval_ms: config.cadence.max_interval_ms,
    };
    let mut policy = IncrementalPolicy::new(config.cadence.incremental);
    let mut tracker = IncrementalTracker::new();
    let req_cfg = RequestConfig {
        top_n: config.engine.top_n,
        max_hops: config.engine.max_hops,
    };
    let cross_chain = build_cross_chain(&config);
    if cross_chain.is_some() {
        tracing::info!("cross-chain detection enabled");
    }

    // The evaluation clock runs at the debounce floor; `cadence.should_send` gates
    // the actual send (on change past the floor, or on the heartbeat ceiling).
    let started = Instant::now();
    let tick = Duration::from_millis(config.cadence.min_interval_ms.max(1));
    let mut interval = tokio::time::interval(tick);
    interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);

    let mut last_sent_ms: Option<u64> = None;
    let mut last_versions: BTreeMap<u64, u64> = BTreeMap::new();
    let mut last_generations: BTreeMap<u64, u64> = handles
        .iter()
        .map(|h| (h.cfg.chain_id, h.generation.load(Ordering::Relaxed)))
        .collect();

    loop {
        tokio::select! {
            _ = shutdown_rx.changed() => {
                if *shutdown_rx.borrow() { return; }
            }
            _ = interval.tick() => {
                if handles.is_empty() { continue; }

                // A reconnect/reseed on any chain starts a fresh engine session →
                // reset the incremental tracker + policy so the next request is full.
                let mut reseeded = false;
                for h in &handles {
                    let g = h.generation.load(Ordering::Relaxed);
                    if last_generations.insert(h.cfg.chain_id, g) != Some(g) {
                        reseeded = true;
                    }
                }
                if reseeded {
                    tracker.reset();
                    policy.reset();
                    tracing::debug!("reseed detected — incremental session reset");
                }

                // O(1) change detection via each mirror's version counter.
                let has_change = handles.iter().any(|h| {
                    last_versions.get(&h.cfg.chain_id).copied() != Some(h.mirror.version())
                });
                let now = started.elapsed().as_millis() as u64;
                if !cadence.should_send(last_sent_ms, now, has_change) {
                    continue;
                }

                // Anchor the end-to-end latency trace at tick start: a wall clock
                // (single-host, comparable to the dashboard) plus a monotonic Instant
                // for the ingestion-side tick_e2e measurement.
                let origin_wall_ms = now_wall_ms();
                let tick_start = Instant::now();

                // HOTPATH_SECONDS measures the intra-process build (snapshot →
                // request), NOT the engine round-trip (that is ENGINE_DETECT_SECONDS).
                let hot_timer = LatencyTimer::start(names::HOTPATH_SECONDS);
                let mut snaps = Vec::new();
                let mut chain_blocks = BTreeMap::new();
                for h in &handles {
                    // Record the version we're about to snapshot at, for next-tick
                    // change detection.
                    last_versions.insert(h.cfg.chain_id, h.mirror.version());
                    let pools = h.mirror.snapshot_verified();
                    metrics::gauge!(names::VERIFIED_POOLS, "chain_id" => h.cfg.chain_id.to_string())
                        .set(pools.len() as f64);
                    if pools.is_empty() { continue; }
                    let ctx = h.ctx_rx.borrow().clone(); // cached — no RPC on the tick
                    if let Some(reason) = hold_back_reason(h.cfg.chain_id, &ctx) {
                        tracing::debug!(chain_id = h.cfg.chain_id, reason, "holding chain back this tick");
                        continue;
                    }
                    let head = pools
                        .iter()
                        .map(|p| &p.blockstamp)
                        .max_by_key(|b| b.number)
                        .unwrap()
                        .clone();
                    chain_blocks.insert(h.cfg.chain_id, head.number);
                    let stamped = re_stamp(&head, pools);
                    snaps.push(ChainSnapshot { context: ctx, pools: stamped });
                }
                if snaps.is_empty() { continue; }

                let incremental = policy.next_incremental();
                for s in &mut snaps {
                    // Always fold state into the tracker — even on a full request — so
                    // the first incremental request after a full one sends only true
                    // deltas instead of re-emitting every pool.
                    let changed = tracker.changed(&s.pools);
                    if incremental {
                        s.pools = changed;
                    }
                }
                let req = build_detect_request(snaps, incremental, cross_chain.clone(), req_cfg);
                last_sent_ms = Some(now);
                let build_ms = hot_timer.elapsed_secs() * 1000.0;
                drop(hot_timer); // stop the hot-path timer before the engine round-trip

                let (resp, roundtrip_ms) = {
                    let detect = LatencyTimer::start(names::ENGINE_DETECT_SECONDS);
                    let resp = engine.detect(&req).await;
                    (resp, detect.elapsed_secs() * 1000.0)
                };
                match resp {
                    Ok(resp) => {
                        // PUBLISH_SECONDS covers response validation → envelope
                        // serialize → sink publish (the last ingestion stage).
                        let publish = LatencyTimer::start(names::PUBLISH_SECONDS);
                        // Drop any invalid opportunity (unverified leg, net_profit==0,
                        // unverified flag, or a blockstamp we never sent) rather than
                        // forwarding a phantom the ingestion layer already knows is bad
                        // (verified honesty). Only the surviving, valid set is published.
                        let before = resp.opportunities.len();
                        let (resp, issues) = retain_valid(&req, &resp);
                        if !issues.is_empty() {
                            let dropped = before - resp.opportunities.len();
                            tracing::warn!(
                                ?issues,
                                dropped,
                                "engine response had issues — dropped invalid opportunities"
                            );
                        }
                        // The stages known at publish time (build + engine round-trip)
                        // ride in the envelope; publish itself is captured only in
                        // Prometheus (it cannot include its own duration in its frame).
                        let latency = Latency::ingestion(
                            origin_wall_ms,
                            vec![
                                Stage::new("build", build_ms),
                                Stage::new("engine_roundtrip", roundtrip_ms),
                            ],
                        );
                        if let Ok(env) = Envelope::opportunities(&resp, chain_blocks.clone()) {
                            let _ = sink.publish(&env.with_latency(latency)).await;
                        }
                        drop(publish); // record PUBLISH_SECONDS before the tick_e2e sample
                        metrics::histogram!(names::TICK_E2E_SECONDS)
                            .record(tick_start.elapsed().as_secs_f64());
                    }
                    Err(e) => tracing::warn!(error = %e, "detect failed — keeping last good snapshot"),
                }
            }
        }
    }
}

/// Wait for SIGINT/SIGTERM (shutdown) or SIGHUP (reload).
async fn wait_for_signals(shutdown_tx: watch::Sender<bool>) {
    #[cfg(unix)]
    {
        use tokio::signal::unix::{signal, SignalKind};
        // A failed signal registration is not fatal — log and fall back to ctrl_c.
        match (
            signal(SignalKind::interrupt()),
            signal(SignalKind::terminate()),
            signal(SignalKind::hangup()),
        ) {
            (Ok(mut sigint), Ok(mut sigterm), Ok(mut sighup)) => loop {
                tokio::select! {
                    _ = sigint.recv() => break,
                    _ = sigterm.recv() => break,
                    _ = sighup.recv() => {
                        tracing::warn!(
                            "SIGHUP received — live config reload is not yet implemented; \
                             restart to apply config.toml changes"
                        );
                    }
                }
            },
            _ => {
                tracing::error!("failed to register unix signal handlers; using ctrl_c only");
                let _ = tokio::signal::ctrl_c().await;
            }
        }
    }
    #[cfg(not(unix))]
    {
        let _ = tokio::signal::ctrl_c().await;
    }
    tracing::info!("shutdown signal received");
    let _ = shutdown_tx.send(true);
}

#[cfg(test)]
mod tests {
    use super::*;

    const ARBITRUM: u64 = 42161;
    const BASE: u64 = 8453; // any OP-Stack chain works; Base is representative

    fn ctx(gas_price_wei: u64, l1_data_fee_wei: u64) -> ChainContext {
        ChainContext {
            chain_id: 0,
            gas_price_wei,
            l1_data_fee_wei,
            base_gas: 150_000,
            per_hop_gas: 100_000,
            gas_safety_multiplier: 1.5,
            min_profit_bps: 5.0,
            native_price_in: Default::default(),
            hubs: Vec::new(),
        }
    }

    #[test]
    fn zero_gas_price_holds_back_any_chain() {
        assert!(hold_back_reason(ARBITRUM, &ctx(0, 0)).is_some());
        assert!(hold_back_reason(BASE, &ctx(0, 1)).is_some());
    }

    #[test]
    fn zero_l1_fee_holds_back_an_op_stack_chain() {
        // Real gas price, but the L1 fee hasn't been read yet — the same
        // phantom-profit risk the gas-price gate exists to prevent (§ pipeline.rs).
        assert!(hold_back_reason(BASE, &ctx(1_000_000, 0)).is_some());
    }

    #[test]
    fn zero_l1_fee_does_not_hold_back_arbitrum() {
        // Arbitrum's l1_data_fee_wei is legitimately always 0 (folded into gas
        // units) — gating on it would hold Arbitrum back forever.
        assert!(hold_back_reason(ARBITRUM, &ctx(1_000_000, 0)).is_none());
    }

    #[test]
    fn real_readings_never_hold_back() {
        assert!(hold_back_reason(ARBITRUM, &ctx(1_000_000, 0)).is_none());
        assert!(hold_back_reason(BASE, &ctx(1_000_000, 2_000)).is_none());
    }

    #[test]
    fn unrecognised_chain_id_is_not_gated_on_l1_fee() {
        // Not in the compiled-in registry -> treated as non-OP-Stack for this
        // check alone; only the universal gas-price gate can hold it back.
        assert!(hold_back_reason(999_999, &ctx(1_000_000, 0)).is_none());
        assert!(hold_back_reason(999_999, &ctx(0, 0)).is_some());
    }
}
