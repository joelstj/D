//! `DetectRequest` and its per-chain gas/price context.

use crate::cross_chain::CrossChain;
use crate::pool::Pool;
use alloy_primitives::Address;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

/// Per-chain gas & price context (`docs/ENGINE_CONTRACT.md §7`).
///
/// Gas values are plain JSON numbers per the contract (L2 wei fits in `u64`); the
/// *pool* big-ints are the ones that must be decimal strings, not these.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ChainContext {
    /// EIP-155 chain id.
    pub chain_id: u64,
    /// L2 execution gas price (`eth_gasPrice` or `baseFee + tip`).
    pub gas_price_wei: u64,
    /// L1 data-availability cost of landing the arb tx (OP-Stack `GasPriceOracle`;
    /// `0` for Arbitrum, whose L1 cost is folded into gas units).
    pub l1_data_fee_wei: u64,
    /// Fixed gas for a bare arb tx (config).
    pub base_gas: u64,
    /// Additional gas per hop (config).
    pub per_hop_gas: u64,
    /// Multiplier applied to the gas estimate for safety (config).
    pub gas_safety_multiplier: f64,
    /// Minimum profit in basis points to report (config).
    pub min_profit_bps: f64,
    /// Numeraire-base-units of token `T` per 1 wei of the native gas token.
    /// A numeraire with no derivable price here cannot be gas-costed and is never
    /// reported by the engine, so it must be omitted rather than guessed.
    pub native_price_in: BTreeMap<Address, f64>,
    /// Numeraire/base tokens to route through. Optional (engine falls back to the
    /// busiest tokens) but set explicitly per chain for determinism.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub hubs: Vec<Address>,
}

/// The request the engine detects opportunities from.
///
/// Field order mirrors `docs/reference/INTEGRATION.md §3`.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct DetectRequest {
    /// Number of top opportunities to return.
    pub top_n: u32,
    /// Maximum hops to consider (`2..=8`).
    pub max_hops: u32,
    /// `true` = only re-scan pools changed since the last call. The first request
    /// of a session must be `false`.
    pub incremental: bool,
    /// Per-chain gas/price context, one entry per chain in the snapshot.
    pub chains: Vec<ChainContext>,
    /// The block-stamped pools to price.
    pub pools: Vec<Pool>,
    /// Optional cross-chain wiring (assets, bridges, pairs).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub cross_chain: Option<CrossChain>,
}

impl DetectRequest {
    /// Serialize to the exact JSON the engine expects.
    pub fn to_engine_json(&self) -> serde_json::Result<String> {
        serde_json::to_string(self)
    }
}
