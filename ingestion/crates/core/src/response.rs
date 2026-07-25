//! `DetectResponse` — the ranked opportunities the engine returns.
//!
//! These types are what the engine *produces*; we consume and relay them. They are
//! therefore modelled leniently where the engine owns the vocabulary (`strategy` is
//! a `String`, `risk.notes` defaults) so a future engine addition never breaks a
//! whole response parse.

use crate::dec::DecU256;
use crate::pool::PoolAddress;
use crate::token::Token;
use alloy_primitives::B256;
use serde::{Deserialize, Serialize};

/// The block an opportunity is priced at. Note the field is `hash` here (the
/// response shape), whereas a request `Blockstamp` uses `block_hash`.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Block {
    /// Chain id.
    pub chain_id: u64,
    /// Block height.
    pub number: u64,
    /// Canonical block hash.
    pub hash: B256,
    /// Block timestamp (unix seconds).
    pub timestamp: u64,
}

/// The engine's risk model output for an opportunity.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Risk {
    /// Probability the fill lands (`0..=1`).
    pub success_probability: f64,
    /// Fraction of the theoretical profit expected to be captured (`0..=1`).
    pub capture_ratio: f64,
    /// Estimated front-running risk (`0..=1`).
    pub frontrun_risk: f64,
    /// Human-readable notes on the scoring.
    #[serde(default)]
    pub notes: Vec<String>,
}

/// One hop of an opportunity's route.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Leg {
    /// The pool used for this hop (contract address or V4 `poolId`).
    pub pool: PoolAddress,
    /// Token going in.
    pub token_in: Token,
    /// Token coming out.
    pub token_out: Token,
    /// Input amount (base units, decimal string).
    pub amount_in: DecU256,
    /// Output amount (base units, decimal string).
    pub amount_out: DecU256,
}

/// A single ranked arbitrage opportunity.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Opportunity {
    /// `two_hop | triangular | multi_hop | cross_chain_two_hop` (engine-owned).
    pub strategy: String,
    /// The numeraire the profit is denominated in.
    pub numeraire: Token,
    /// Optimal input amount.
    pub input_amount: DecU256,
    /// Resulting output amount.
    pub output_amount: DecU256,
    /// Gross profit before costs.
    pub gross_profit: DecU256,
    /// L2 execution + L1 data gas cost, in the numeraire.
    pub gas_cost: DecU256,
    /// Bridge cost, in the numeraire (`"0"` for same-chain).
    pub bridge_cost: DecU256,
    /// `gross - gas - bridge`; always `> 0` when reported.
    pub net_profit: DecU256,
    /// Net profit in basis points of input.
    pub profit_bps: f64,
    /// Risk-adjusted expected net (base units).
    pub expected_net: DecU256,
    /// Ranking key — `expected_net` as a float (risk-adjusted expected value).
    pub score: f64,
    /// Number of hops.
    pub hops: u32,
    /// Chains involved.
    pub chain_ids: Vec<u64>,
    /// `true` for cross-chain opportunities.
    pub is_cross_chain: bool,
    /// Expected settlement time (seconds); `0` for same-chain.
    pub settle_seconds: u64,
    /// `true` only if every leg's pool was `verified` in the request.
    pub verified: bool,
    /// The block the opportunity is priced at.
    pub block: Block,
    /// Risk model output.
    pub risk: Risk,
    /// The route.
    pub legs: Vec<Leg>,
}

/// The engine's response: a de-duplicated, `score`-ordered opportunity list.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct DetectResponse {
    /// `opportunities.len()`.
    pub count: u32,
    /// Ranked opportunities (ordered by `score`, do not re-sort).
    pub opportunities: Vec<Opportunity>,
    /// Optional engine-internal stage timing for the latency-health pipeline
    /// (`{component, stages:[{stage, ms}], total_ms}`). The engine owns this
    /// vocabulary, so — like `strategy` and `risk.notes` — we model it leniently as
    /// an opaque value and **relay it verbatim** into the output envelope's payload
    /// so the dashboard can attribute latency to the engine's internal stages. Absent
    /// on responses from an engine that predates the field.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub timing: Option<serde_json::Value>,
}

impl DetectResponse {
    /// Parse a response from the engine's JSON.
    pub fn from_engine_json(s: &str) -> serde_json::Result<Self> {
        serde_json::from_str(s)
    }
}

/// The error shape the engine returns on a malformed request (exit 1 / non-200):
/// `{"error": "...", "type": "..."}` (`docs/reference/INTEGRATION.md §1`).
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct EngineError {
    /// Human-readable error message.
    pub error: String,
    /// Error type/category.
    #[serde(rename = "type")]
    pub error_type: String,
}
