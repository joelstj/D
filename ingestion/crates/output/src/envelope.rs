//! The stable, versioned output envelope (`docs/ARCHITECTURE.md §10`).
//!
//! Everything the component fans out to the GUI + execution engine is wrapped in
//! `{ schema_version, kind, chain_blocks, payload }`, so consumers subscribe with
//! no coupling to our internals.

use l2i_core::{DetectResponse, Pool, SCHEMA_VERSION};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

/// What an envelope carries.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EnvelopeKind {
    /// A pool-state snapshot.
    Snapshot,
    /// Ranked arbitrage opportunities.
    Opportunities,
}

/// One measured processing stage (milliseconds), part of a [`Latency`] block.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Stage {
    /// Stage name (e.g. `"build"`, `"engine_roundtrip"`).
    pub stage: String,
    /// Elapsed time in milliseconds.
    pub ms: f64,
}

impl Stage {
    /// Build a stage from a name and a duration in milliseconds.
    pub fn new(stage: impl Into<String>, ms: f64) -> Self {
        Self {
            stage: stage.into(),
            ms,
        }
    }
}

/// The ingestion component's latency contribution + the single-host end-to-end
/// anchor, carried in the envelope for the latency-health pipeline (root
/// `CLAUDE.md`). Purely additive observability — a consumer that ignores it sees
/// exactly the previous envelope.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Latency {
    /// Wall-clock (`SystemTime`) milliseconds at aggregator tick start. Because the
    /// whole stack runs on one host, the dashboard measures end-to-end latency as
    /// `now_ms - origin_wall_ms`; it is **not** a monotonic duration and is only
    /// meaningful within a single host's clock.
    pub origin_wall_ms: u64,
    /// The component these stages belong to (always `"ingestion"` here).
    pub component: String,
    /// Per-stage durations known at publish time (`build`, `engine_roundtrip`).
    pub stages: Vec<Stage>,
    /// Sum of `stages` (milliseconds).
    pub total_ms: f64,
}

impl Latency {
    /// Assemble the ingestion latency block, computing `total_ms` from `stages`.
    pub fn ingestion(origin_wall_ms: u64, stages: Vec<Stage>) -> Self {
        let total_ms = stages.iter().map(|s| s.ms).sum();
        Self {
            origin_wall_ms,
            component: "ingestion".to_string(),
            stages,
            total_ms,
        }
    }
}

/// The versioned wire envelope.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Envelope {
    /// The wire-contract version (`l2i_core::SCHEMA_VERSION`).
    pub schema_version: u32,
    /// What `payload` is.
    pub kind: EnvelopeKind,
    /// Per-chain block heights this message reflects (`chain_id → number`).
    pub chain_blocks: BTreeMap<u64, u64>,
    /// The typed payload (a pool array, or a `DetectResponse`).
    pub payload: serde_json::Value,
    /// Optional latency trace (ingestion stages + end-to-end anchor). Additive and
    /// backward-compatible: omitted from the wire when absent, so pre-existing
    /// consumers are unaffected (`schema_version` is unchanged).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub latency: Option<Latency>,
}

impl Envelope {
    /// Wrap a pool-state snapshot.
    pub fn snapshot(pools: &[Pool]) -> serde_json::Result<Self> {
        let chain_blocks = chain_blocks_from_pools(pools);
        Ok(Self {
            schema_version: SCHEMA_VERSION,
            kind: EnvelopeKind::Snapshot,
            chain_blocks,
            payload: serde_json::to_value(pools)?,
            latency: None,
        })
    }

    /// Wrap ranked opportunities, tagged with the block heights they priced at.
    pub fn opportunities(
        resp: &DetectResponse,
        chain_blocks: BTreeMap<u64, u64>,
    ) -> serde_json::Result<Self> {
        Ok(Self {
            schema_version: SCHEMA_VERSION,
            kind: EnvelopeKind::Opportunities,
            chain_blocks,
            payload: serde_json::to_value(resp)?,
            latency: None,
        })
    }

    /// Attach a latency trace, consuming and returning the envelope (builder style).
    #[must_use]
    pub fn with_latency(mut self, latency: Latency) -> Self {
        self.latency = Some(latency);
        self
    }

    /// Serialize to a single NDJSON line (no embedded newlines).
    pub fn to_ndjson(&self) -> serde_json::Result<String> {
        serde_json::to_string(self)
    }
}

/// The freshest block height per chain across `pools`.
pub fn chain_blocks_from_pools(pools: &[Pool]) -> BTreeMap<u64, u64> {
    let mut m: BTreeMap<u64, u64> = BTreeMap::new();
    for p in pools {
        let e = m.entry(p.blockstamp.chain_id).or_insert(0);
        *e = (*e).max(p.blockstamp.number);
    }
    m
}
