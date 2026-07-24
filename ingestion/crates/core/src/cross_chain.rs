//! The optional `cross_chain` block: canonical assets, bridge economics, and the
//! (asset, numeraire) pairs to scan (`docs/ENGINE_CONTRACT.md §8`).

use crate::token::Token;
use serde::{Deserialize, Serialize};

/// One same-asset representation on a specific chain.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Representation {
    /// The token on this chain. Carries only `{chain_id, address, decimals}` — no
    /// symbol — matching the contract's representation shape.
    pub token: Token,
    /// `true` = the canonical/native representation on that chain (not a wrapped
    /// bridge asset).
    pub native: bool,
    /// `true` = a configured bridge can move it.
    pub bridgeable: bool,
}

/// A canonical asset (e.g. `WETH`) and its per-chain representations.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Asset {
    /// Canonical symbol, e.g. `"WETH"`.
    pub symbol: String,
    /// Its representation on each chain that has one.
    pub representations: Vec<Representation>,
}

/// The real economics of one directed bridge route. These are config (sourced from
/// bridge docs, reviewed), not on-chain-derivable.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Bridge {
    /// Canonical asset symbol this route moves.
    pub symbol: String,
    /// Source chain id.
    pub from_chain: u64,
    /// Destination chain id.
    pub to_chain: u64,
    /// Proportional fee in basis points.
    pub fee_bps: f64,
    /// Fixed fee in the asset's base units.
    pub fixed_fee: u64,
    /// Expected settlement time in seconds (drives the engine's risk model).
    pub settle_seconds: u64,
}

/// Cross-chain detection wiring.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct CrossChain {
    /// Canonical assets and their per-chain representations.
    pub assets: Vec<Asset>,
    /// Supported directed bridge routes.
    pub bridges: Vec<Bridge>,
    /// `(asset, numeraire)` canonical-symbol pairs to scan.
    pub pairs: Vec<[String; 2]>,
}
