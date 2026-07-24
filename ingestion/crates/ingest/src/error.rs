//! Ingest errors.

/// An error decoding an event or seeding/updating the mirror.
#[derive(Debug, thiserror::Error)]
pub enum IngestError {
    /// An RPC read failed.
    #[error("rpc: {0}")]
    Rpc(#[from] l2i_rpc::RpcError),
    /// An event/return could not be decoded.
    #[error("decode: {0}")]
    Decode(String),
    /// A seed sub-call reverted.
    #[error("seed read for pool #{0} reverted")]
    SeedReverted(usize),
    /// The mirror had no entry for a pool a log referenced.
    #[error("no mirror entry for pool {0}")]
    UnknownPool(String),
}

/// Convenience alias.
pub type Result<T> = std::result::Result<T, IngestError>;
