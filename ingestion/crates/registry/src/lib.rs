//! # l2i-registry
//!
//! The per-chain pool registry ([`schema`]) and the on-chain **validation gate**
//! ([`gate`]) that proves every configured address is current and correct before it
//! enters the live set — the mechanism that makes "every route, address, connection
//! is validated on-chain" a tested guarantee rather than a hope
//! (`docs/ARCHITECTURE.md §7`).

pub mod abi;
pub mod error;
pub mod gate;
pub mod schema;

pub use error::{GateError, RejectReason};
pub use gate::{
    validate_pool, validate_registry, GateOutcome, GatePolicy, Rejected, ValidatedPool,
    ValidatedToken,
};
pub use schema::{parse_registry, PoolEntry, PoolRegistry, V2V3Entry, V4Entry};

use std::path::Path;

/// Load and parse a per-chain registry file (`config/pools/<chain>.toml`).
pub fn load_registry_file(path: impl AsRef<Path>) -> Result<PoolRegistry, RegistryLoadError> {
    let path = path.as_ref();
    let src = std::fs::read_to_string(path).map_err(|e| RegistryLoadError::Io {
        path: path.display().to_string(),
        source: e,
    })?;
    parse_registry(&src).map_err(|e| RegistryLoadError::Parse {
        path: path.display().to_string(),
        source: e,
    })
}

/// An error loading a registry file.
#[derive(Debug, thiserror::Error)]
pub enum RegistryLoadError {
    /// The file could not be read.
    #[error("reading registry {path}: {source}")]
    Io {
        /// The path we tried to read.
        path: String,
        /// The underlying IO error.
        source: std::io::Error,
    },
    /// The file was not valid registry TOML.
    #[error("parsing registry {path}: {source}")]
    Parse {
        /// The path we tried to parse.
        path: String,
        /// The underlying TOML error.
        source: toml::de::Error,
    },
}
