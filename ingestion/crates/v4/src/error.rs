//! V4 adapter errors.

/// An error decoding a V4 event or reading `StateView`.
#[derive(Debug, thiserror::Error)]
pub enum V4Error {
    /// An RPC read failed.
    #[error("rpc: {0}")]
    Rpc(#[from] l2i_rpc::RpcError),
    /// An event/return could not be decoded.
    #[error("decode: {0}")]
    Decode(String),
    /// A `StateView` seed sub-call reverted.
    #[error("StateView read for pool #{0} reverted")]
    SeedReverted(usize),
}

/// Convenience alias.
pub type Result<T> = std::result::Result<T, V4Error>;
