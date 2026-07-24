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
        })
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
