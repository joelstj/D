//! # l2i-output — outbound fan-out
//!
//! The plug-and-play integration surface (`docs/ARCHITECTURE.md §10`): a stable,
//! versioned [`Envelope`] pushed to a configured [`OutputSink`].
//!
//! - [`envelope`] — the `{schema_version, kind, chain_blocks, payload}` wire shape.
//! - [`sink`] — the [`OutputSink`](sink::OutputSink) trait + [`StdoutSink`].
//! - [`ws`] — [`WsServerSink`](ws::WsServerSink), the default WebSocket broadcast.
//!
//! Redis and gRPC sinks are part of the config surface but not built in this
//! milestone; [`sink_from_config`] returns an [`OutputError::Unavailable`] for them
//! so the gap is loud, never silent.

pub mod envelope;
pub mod sink;
pub mod ws;

pub use envelope::{Envelope, EnvelopeKind};
pub use sink::{OutputError, OutputSink, Result, StdoutSink};
pub use ws::WsServerSink;

/// Build a sink from the configured kind (`ws` | `stdout` | `redis` | `grpc`).
///
/// `ws` and `stdout` are built; `redis`/`grpc` are declared in the config surface
/// but not implemented in this build and return [`OutputError::Unavailable`].
pub async fn sink_from_config(kind: &str, bind: &str) -> Result<Box<dyn OutputSink>> {
    match kind {
        "ws" => Ok(Box::new(WsServerSink::bind(bind).await?)),
        "stdout" => Ok(Box::new(StdoutSink)),
        "redis" | "grpc" => Err(OutputError::Unavailable(format!(
            "sink '{kind}' is in the config surface but not implemented in this build"
        ))),
        other => Err(OutputError::Unavailable(format!("unknown sink '{other}'"))),
    }
}
