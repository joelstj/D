//! # l2i-observability
//!
//! The eyes on the hot path (`docs/ARCHITECTURE.md §10`): Prometheus `/metrics`,
//! a `/health` endpoint, latency-histogram helpers, and structured `tracing`
//! setup. One small HTTP server exposes health + metrics.

use axum::{routing::get, Json, Router};
use metrics_exporter_prometheus::{PrometheusBuilder, PrometheusHandle};
use std::net::SocketAddr;
use std::time::Instant;

/// Metric names, kept in one place so dashboards and code agree.
pub mod names {
    /// Histogram: intra-process hot-path latency, decode→emit (seconds).
    pub const HOTPATH_SECONDS: &str = "l2i_hotpath_seconds";
    /// Histogram: engine `/detect` round-trip latency (seconds).
    pub const ENGINE_DETECT_SECONDS: &str = "l2i_engine_detect_seconds";
    /// Counter: reconciliation mismatches detected.
    pub const RECONCILE_MISMATCHES: &str = "l2i_reconcile_mismatches_total";
    /// Counter: reorgs handled.
    pub const REORGS_TOTAL: &str = "l2i_reorgs_total";
    /// Counter: head gaps (missed heads) detected and backfilled.
    pub const HEAD_GAPS: &str = "l2i_head_gaps_total";
    /// Counter: ingestor reconnects (a chain's live loop dropped and was restarted).
    pub const INGESTOR_RECONNECTS: &str = "l2i_ingestor_reconnects_total";
    /// Gauge: pools currently `verified` per chain.
    pub const VERIFIED_POOLS: &str = "l2i_verified_pools";
    /// Gauge: chains currently live (connected + seeded).
    pub const CHAINS_LIVE: &str = "l2i_chains_live";
    /// Gauge: output subscribers currently connected.
    pub const OUTPUT_SUBSCRIBERS: &str = "l2i_output_subscribers";
    /// Counter: opportunities dropped to a lagging (slow-consumer) subscriber.
    pub const OUTPUT_LAGGED_DROPS: &str = "l2i_output_lagged_drops_total";
}

/// Install the global Prometheus recorder, returning a render handle. Can be called
/// only once per process (returns `Err` if a recorder is already installed).
pub fn install_metrics() -> Result<PrometheusHandle, String> {
    PrometheusBuilder::new()
        .install_recorder()
        .map_err(|e| e.to_string())
}

/// A scoped timer that records elapsed seconds into a histogram on drop.
pub struct LatencyTimer {
    name: &'static str,
    start: Instant,
}

impl LatencyTimer {
    /// Start timing for `histogram_name`.
    pub fn start(histogram_name: &'static str) -> Self {
        Self {
            name: histogram_name,
            start: Instant::now(),
        }
    }

    /// Elapsed so far (seconds).
    pub fn elapsed_secs(&self) -> f64 {
        self.start.elapsed().as_secs_f64()
    }
}

impl Drop for LatencyTimer {
    fn drop(&mut self) {
        metrics::histogram!(self.name).record(self.start.elapsed().as_secs_f64());
    }
}

/// The health + metrics HTTP router.
pub fn router(handle: PrometheusHandle) -> Router {
    Router::new()
        .route(
            "/health",
            get(|| async { Json(serde_json::json!({ "status": "ok" })) }),
        )
        .route(
            "/metrics",
            get(move || {
                let handle = handle.clone();
                async move { handle.render() }
            }),
        )
}

/// Bind and serve the health + metrics router; returns the bound address. The
/// server runs in a background task.
pub async fn serve(bind: &str, router: Router) -> Result<SocketAddr, String> {
    let listener = tokio::net::TcpListener::bind(bind)
        .await
        .map_err(|e| format!("bind {bind}: {e}"))?;
    let addr = listener.local_addr().map_err(|e| e.to_string())?;
    tokio::spawn(async move {
        if let Err(e) = axum::serve(listener, router).await {
            tracing::error!(error = %e, "observability server exited");
        }
    });
    Ok(addr)
}

/// Initialise structured logging. Best-effort — if a global subscriber is already
/// set (e.g. in tests), this is a no-op.
pub fn init_tracing(level: &str, json: bool) {
    use tracing_subscriber::{fmt, prelude::*, EnvFilter};
    let filter = EnvFilter::try_new(level).unwrap_or_else(|_| EnvFilter::new("info"));
    let registry = tracing_subscriber::registry().with(filter);
    let _ = if json {
        registry.with(fmt::layer().json()).try_init()
    } else {
        registry.with(fmt::layer()).try_init()
    };
}
