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

#[cfg(test)]
mod tests {
    use super::*;

    /// Every shipped `*.example.toml` pool registry must load and parse cleanly
    /// with `>=1` pool — this is what `config.example.toml`'s `pool_registry`
    /// fields point at (once copied), and what `scripts/discover_pools.py`
    /// writes for chains a user hasn't curated yet. A regression here means a
    /// freshly-generated or freshly-copied config is broken before it ever
    /// reaches the on-chain validation gate.
    #[test]
    fn every_shipped_example_registry_loads_with_pools() {
        let dir = concat!(env!("CARGO_MANIFEST_DIR"), "/../../config/pools");
        let mut checked = 0;
        for entry in std::fs::read_dir(dir).expect("config/pools exists") {
            let entry = entry.unwrap();
            let path = entry.path();
            if path.extension().and_then(|e| e.to_str()) != Some("toml") {
                continue;
            }
            let reg = load_registry_file(&path)
                .unwrap_or_else(|e| panic!("{} failed to load: {e}", path.display()));
            assert!(
                !reg.pools.is_empty(),
                "{} has zero pools — not a useful example",
                path.display()
            );
            checked += 1;
        }
        assert!(
            checked >= 3,
            "expected at least arbitrum/base/optimism examples, found {checked}"
        );
    }
}
