//! `DetectRequest` assembly and the per-session incremental policy.

use l2i_core::{ChainContext, CrossChain, DetectRequest, Pool};

/// Static request knobs (`top_n`, `max_hops`).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct RequestConfig {
    /// Number of top opportunities to return.
    pub top_n: u32,
    /// Maximum hops (`2..=8`).
    pub max_hops: u32,
}

impl Default for RequestConfig {
    fn default() -> Self {
        Self {
            top_n: 10,
            max_hops: 4,
        }
    }
}

/// One chain's contribution to a request: its context and its (already re-stamped)
/// pools.
#[derive(Clone, Debug)]
pub struct ChainSnapshot {
    /// Gas/price context for the chain.
    pub context: ChainContext,
    /// The chain's pools (all sharing the chain's head blockstamp).
    pub pools: Vec<Pool>,
}

/// Assemble a [`DetectRequest`] from per-chain snapshots.
pub fn build_detect_request(
    chains: Vec<ChainSnapshot>,
    incremental: bool,
    cross_chain: Option<CrossChain>,
    cfg: RequestConfig,
) -> DetectRequest {
    let mut contexts = Vec::with_capacity(chains.len());
    let mut pools = Vec::new();
    for c in chains {
        contexts.push(c.context);
        pools.extend(c.pools);
    }
    DetectRequest {
        top_n: cfg.top_n,
        max_hops: cfg.max_hops,
        incremental,
        chains: contexts,
        pools,
        cross_chain,
    }
}

/// Tracks a session's incremental policy: the **first** request of a session is
/// always `incremental:false` (a full snapshot); subsequent requests follow the
/// configured setting.
#[derive(Clone, Copy, Debug)]
pub struct IncrementalPolicy {
    sent_first: bool,
    incremental_after_first: bool,
}

impl IncrementalPolicy {
    /// A new policy; `incremental_after_first` = whether to send deltas after the
    /// first full request.
    pub fn new(incremental_after_first: bool) -> Self {
        Self {
            sent_first: false,
            incremental_after_first,
        }
    }

    /// The `incremental` flag for the next request, advancing the session state.
    pub fn next_incremental(&mut self) -> bool {
        if !self.sent_first {
            self.sent_first = true;
            false
        } else {
            self.incremental_after_first
        }
    }

    /// Reset to "first request not yet sent" (call on reconnect / reseed so the
    /// next request is a full snapshot again).
    pub fn reset(&mut self) {
        self.sent_first = false;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn first_request_is_always_full() {
        let mut p = IncrementalPolicy::new(true);
        assert!(
            !p.next_incremental(),
            "first request must be incremental:false"
        );
        assert!(p.next_incremental(), "second follows config (true)");
        assert!(p.next_incremental());
        p.reset();
        assert!(!p.next_incremental(), "after reset the next is full again");
    }

    #[test]
    fn incremental_disabled_stays_false() {
        let mut p = IncrementalPolicy::new(false);
        assert!(!p.next_incremental());
        assert!(!p.next_incremental());
    }
}
