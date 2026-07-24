//! Atomic per-chain snapshotting and incremental-delta tracking.
//!
//! Two "in sync" guarantees (`docs/ENGINE_CONTRACT.md §5`):
//! 1. **Intra-chain consistency** — every pool of chain C in one request carries the
//!    *same* blockstamp. Since the mirror is always up to date, a pool whose state
//!    last changed at block M < head H still holds its true state at H, so we
//!    re-stamp all of a chain's pools to the chain's head blockstamp.
//! 2. **Incremental** — after the first request we send only the pools whose priced
//!    state actually changed, via a per-pool fingerprint that excludes the
//!    blockstamp (a re-stamp alone is not a change the engine must re-scan).

use l2i_core::{Blockstamp, Pool, PoolAddress};
use std::collections::hash_map::DefaultHasher;
use std::collections::HashMap;
use std::hash::{Hash, Hasher};

/// Re-stamp every pool to `head` — enforces one block per chain per request.
pub fn re_stamp(head: &Blockstamp, mut pools: Vec<Pool>) -> Vec<Pool> {
    for p in &mut pools {
        p.blockstamp = head.clone();
    }
    pools
}

/// Fingerprint the engine-relevant *priced state* of a pool (identity, kind, fee,
/// verified, and the reserves / slot values) — deliberately excluding the
/// blockstamp and token metadata, which don't change what the engine must re-scan.
pub fn state_fingerprint(p: &Pool) -> u64 {
    let mut h = DefaultHasher::new();
    p.address.hash(&mut h);
    (p.kind as u8).hash(&mut h);
    p.fee_pips.hash(&mut h);
    p.verified.hash(&mut h);
    if let Some(v2) = &p.v2 {
        v2.reserve0.0.hash(&mut h);
        v2.reserve1.0.hash(&mut h);
    }
    if let Some(v3) = &p.v3 {
        v3.sqrt_price_x96.0.hash(&mut h);
        v3.tick.hash(&mut h);
        v3.liquidity.0.hash(&mut h);
    }
    h.finish()
}

/// Remembers each pool's last-sent fingerprint, so `changed` yields only the pools
/// the engine must re-scan.
#[derive(Default)]
pub struct IncrementalTracker {
    seen: HashMap<PoolAddress, u64>,
}

impl IncrementalTracker {
    /// A fresh tracker (nothing seen → the first `changed` returns everything).
    pub fn new() -> Self {
        Self::default()
    }

    /// The subset of `pools` new or changed since last call; records new state.
    pub fn changed(&mut self, pools: &[Pool]) -> Vec<Pool> {
        let mut out = Vec::new();
        for p in pools {
            let fp = state_fingerprint(p);
            if self.seen.insert(p.address, fp) != Some(fp) {
                out.push(p.clone());
            }
        }
        out
    }

    /// Forget everything (call on reseed / reconnect so the next request is full).
    pub fn reset(&mut self) {
        self.seen.clear();
    }

    /// How many pools are being tracked.
    pub fn len(&self) -> usize {
        self.seen.len()
    }

    /// Whether nothing is tracked yet.
    pub fn is_empty(&self) -> bool {
        self.seen.is_empty()
    }
}
