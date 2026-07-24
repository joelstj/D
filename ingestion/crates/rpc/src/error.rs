//! Error type shared across the RPC layer.

use std::time::Duration;

/// An RPC / transport error.
#[derive(Debug, thiserror::Error)]
pub enum RpcError {
    /// The underlying transport failed (connect, socket, TLS…).
    #[error("transport: {0}")]
    Transport(String),
    /// The node returned a JSON-RPC error, or the call otherwise failed.
    #[error("rpc call failed: {0}")]
    Call(String),
    /// A response could not be ABI/JSON decoded into the expected shape.
    #[error("decode: {0}")]
    Decode(String),
    /// A subscription stream ended unexpectedly.
    #[error("subscription closed")]
    SubscriptionClosed,
    /// A call exceeded its deadline.
    #[error("timed out after {0:?}")]
    Timeout(Duration),
    /// A Multicall3 sub-call reverted (and failure was not allowed).
    #[error("multicall sub-call #{index} reverted")]
    MulticallReverted {
        /// Index of the reverting sub-call.
        index: usize,
    },
}

/// Convenience alias.
pub type Result<T> = std::result::Result<T, RpcError>;
