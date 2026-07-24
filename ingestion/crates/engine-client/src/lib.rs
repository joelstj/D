//! # l2i-engine-client
//!
//! The decoupled interface to the `l2arb` detection engine. Everything downstream
//! programs against the [`EngineClient`] trait, so HTTP vs subprocess (or a future
//! gRPC) is a config choice and the same component works whether `l2arb` runs
//! locally, in a sidecar, or over the network (`docs/ARCHITECTURE.md §10`).
//!
//! - [`HttpEngineClient`] — the default hot-path impl: a keep-alive `reqwest` pool
//!   `POST /detect` / `GET /health` against a long-running FastAPI service.
//! - [`SubprocessEngineClient`] — one request → one response via
//!   `python -m l2arb.api.runner`; fine for batch/CLI, not the high-frequency path.
//! - [`validate_response`] — the response-handling checks from
//!   `docs/ENGINE_CONTRACT.md §10`.

mod http;
mod subprocess;
mod validate;

pub use http::{HttpConfig, HttpEngineClient};
pub use subprocess::SubprocessEngineClient;
pub use validate::{validate_response, ResponseIssue};

use async_trait::async_trait;
use l2i_core::{DetectRequest, DetectResponse};

/// An error talking to the engine.
#[derive(Debug, thiserror::Error)]
pub enum EngineError {
    /// The transport (HTTP/subprocess) failed.
    #[error("transport: {0}")]
    Transport(String),
    /// The engine returned a non-success status / exit code.
    #[error("engine returned an error: {0}")]
    Engine(String),
    /// A response could not be parsed as the documented shape.
    #[error("decode: {0}")]
    Decode(String),
    /// The call timed out.
    #[error("timed out")]
    Timeout,
}

/// Convenience alias.
pub type Result<T> = std::result::Result<T, EngineError>;

/// The engine, behind one interface.
#[async_trait]
pub trait EngineClient: Send + Sync {
    /// Gate startup/reconnect on this: `true` iff the engine reports healthy.
    async fn health(&self) -> Result<bool>;

    /// Run detection for `req`, returning the ranked opportunities.
    async fn detect(&self, req: &DetectRequest) -> Result<DetectResponse>;
}
