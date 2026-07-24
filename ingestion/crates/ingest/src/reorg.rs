//! Reorg detection (`docs/ARCHITECTURE.md §6`).
//!
//! We keep a short ring of recent `(number, hash, parent_hash)` per chain. If a new
//! head's `parent_hash` doesn't match our stored hash for `number−1` (or a head
//! arrives at/behind a height with a different hash), a reorg occurred: we report
//! the common ancestor so the ingestor can mark every pool touched after it
//! `verified:false`, re-derive from canonical logs, then restore `verified:true`.
//! L2 reorgs are rare but real (sequencer hiccups); we handle them, not assume they
//! can't happen.

use alloy_primitives::B256;
use std::collections::VecDeque;

/// A minimal block reference for reorg tracking.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct BlockRef {
    /// Block height.
    pub number: u64,
    /// Block hash.
    pub hash: B256,
    /// Parent block hash.
    pub parent_hash: B256,
}

/// The outcome of observing a new head.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ReorgOutcome {
    /// The canonical chain advanced by one block (parent matches).
    Extended,
    /// The same head was seen again.
    Duplicate,
    /// A reorg: the canonical chain diverged; `common_ancestor` is the last block
    /// number still valid. All pools touched *after* it must go `verified:false`.
    Reorg {
        /// Highest still-canonical block number.
        common_ancestor: u64,
    },
    /// A gap: heads were missed (`expected` next, `got` instead). The ingestor
    /// should backfill logs for the skipped range.
    Gap {
        /// The height we expected next.
        expected: u64,
        /// The height we actually got.
        got: u64,
    },
}

/// A per-chain reorg tracker over a short ring of recent heads.
#[derive(Debug)]
pub struct ReorgTracker {
    recent: VecDeque<BlockRef>,
    capacity: usize,
}

impl ReorgTracker {
    /// A tracker retaining `capacity` recent heads (e.g. 64).
    pub fn new(capacity: usize) -> Self {
        Self {
            recent: VecDeque::with_capacity(capacity.max(1)),
            capacity: capacity.max(1),
        }
    }

    /// The most recent observed head.
    pub fn head(&self) -> Option<BlockRef> {
        self.recent.back().copied()
    }

    fn push(&mut self, b: BlockRef) {
        self.recent.push_back(b);
        while self.recent.len() > self.capacity {
            self.recent.pop_front();
        }
    }

    /// After a reorg, reset the ring to the new head so subsequent observes track
    /// the new canonical chain.
    fn reset_to(&mut self, b: BlockRef) {
        self.recent.clear();
        self.push(b);
    }

    /// Observe a new head, classifying it.
    pub fn observe(&mut self, head: BlockRef) -> ReorgOutcome {
        let Some(last) = self.recent.back().copied() else {
            self.push(head);
            return ReorgOutcome::Extended;
        };

        // Same height.
        if head.number == last.number {
            if head.hash == last.hash {
                return ReorgOutcome::Duplicate;
            }
            let ca = head.number.saturating_sub(1);
            self.reset_to(head);
            return ReorgOutcome::Reorg {
                common_ancestor: ca,
            };
        }

        // Next height.
        if head.number == last.number + 1 {
            if head.parent_hash == last.hash {
                self.push(head);
                return ReorgOutcome::Extended;
            }
            // Parent doesn't match → our block at `last.number` was orphaned.
            let ca = last.number.saturating_sub(1);
            self.reset_to(head);
            return ReorgOutcome::Reorg {
                common_ancestor: ca,
            };
        }

        // Jumped ahead → a gap.
        if head.number > last.number + 1 {
            self.push(head);
            return ReorgOutcome::Gap {
                expected: last.number + 1,
                got: head.number,
            };
        }

        // Behind our current head. This is a reorg *only* if the block we already
        // stored at this height differs. A provider re-delivering an **identical**
        // old head — routine on WS reconnect / load-balancer failover / duplicate
        // notifications — changed nothing, and must not spuriously invalidate
        // verified pools or reset the ring (which would also cascade a bogus `Gap`
        // on the next real head).
        match self.recent.iter().find(|e| e.number == head.number) {
            Some(stored) if stored.hash == head.hash => ReorgOutcome::Duplicate,
            Some(_) => {
                // A *different* block at a known past height → a genuine reorg there.
                let ca = head.number.saturating_sub(1);
                self.reset_to(head);
                ReorgOutcome::Reorg {
                    common_ancestor: ca,
                }
            }
            None => {
                // Older than our retained window: we cannot prove divergence here,
                // and a real reorg this deep would resurface through the forward
                // heads on the new chain (and is caught by reconciliation). Treat it
                // as a benign stale re-delivery rather than cascade a spurious reorg.
                ReorgOutcome::Duplicate
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn b(n: u64, h: u8, p: u8) -> BlockRef {
        BlockRef {
            number: n,
            hash: B256::from([h; 32]),
            parent_hash: B256::from([p; 32]),
        }
    }

    #[test]
    fn extends_on_matching_parent() {
        let mut t = ReorgTracker::new(8);
        assert_eq!(t.observe(b(100, 1, 0)), ReorgOutcome::Extended);
        assert_eq!(t.observe(b(101, 2, 1)), ReorgOutcome::Extended);
        assert_eq!(t.observe(b(102, 3, 2)), ReorgOutcome::Extended);
    }

    #[test]
    fn duplicate_head_detected() {
        let mut t = ReorgTracker::new(8);
        t.observe(b(100, 1, 0));
        assert_eq!(t.observe(b(100, 1, 0)), ReorgOutcome::Duplicate);
    }

    #[test]
    fn reorg_on_parent_mismatch() {
        let mut t = ReorgTracker::new(8);
        t.observe(b(100, 1, 0));
        t.observe(b(101, 2, 1));
        // New 102 whose parent (101) hash differs from our block-101 hash (2).
        assert_eq!(
            t.observe(b(102, 9, 8)),
            ReorgOutcome::Reorg {
                common_ancestor: 100
            }
        );
        // Tracker now follows the new chain.
        assert_eq!(t.head().unwrap().number, 102);
    }

    #[test]
    fn reorg_same_height_different_hash() {
        let mut t = ReorgTracker::new(8);
        t.observe(b(100, 1, 0));
        t.observe(b(101, 2, 1));
        assert_eq!(
            t.observe(b(101, 7, 6)),
            ReorgOutcome::Reorg {
                common_ancestor: 100
            }
        );
    }

    #[test]
    fn gap_detected() {
        let mut t = ReorgTracker::new(8);
        t.observe(b(100, 1, 0));
        assert_eq!(
            t.observe(b(105, 5, 4)),
            ReorgOutcome::Gap {
                expected: 101,
                got: 105
            }
        );
    }

    #[test]
    fn reobserved_identical_old_head_is_duplicate_not_reorg() {
        // A provider re-delivers an OLD head identical to what we already stored
        // (WS reconnect / failover). Nothing reorged → must be Duplicate, and the
        // ring must keep tracking the real tip (102) so no bogus Gap follows.
        let mut t = ReorgTracker::new(64);
        t.observe(b(100, 1, 0));
        t.observe(b(101, 2, 1));
        t.observe(b(102, 3, 2)); // tip = 102
        assert_eq!(
            t.observe(b(101, 2, 1)),
            ReorgOutcome::Duplicate,
            "identical old head must not be a spurious reorg"
        );
        assert_eq!(t.head().unwrap().number, 102, "tip is preserved");
        // The next real head still extends cleanly — no cascading gap.
        assert_eq!(t.observe(b(103, 4, 3)), ReorgOutcome::Extended);
    }

    #[test]
    fn reobserved_old_height_different_hash_is_reorg() {
        // A DIFFERENT block at a known past height IS a genuine reorg there.
        let mut t = ReorgTracker::new(64);
        t.observe(b(100, 1, 0));
        t.observe(b(101, 2, 1));
        t.observe(b(102, 3, 2)); // tip = 102
        assert_eq!(
            t.observe(b(101, 77, 1)), // height 101, but a different hash (77 vs 2)
            ReorgOutcome::Reorg {
                common_ancestor: 100
            }
        );
        assert_eq!(
            t.head().unwrap().number,
            101,
            "tracker follows the new chain"
        );
    }

    #[test]
    fn stale_head_below_window_is_benign_duplicate() {
        // A head older than the retained ring window cannot be proven divergent and
        // must not cascade a spurious reorg — a real deep reorg surfaces via the
        // forward heads (and reconciliation). Ring capacity 2 keeps only the last 2.
        let mut t = ReorgTracker::new(2);
        t.observe(b(100, 1, 0));
        t.observe(b(101, 2, 1));
        t.observe(b(102, 3, 2)); // ring now holds {101, 102}; 100 evicted
        assert_eq!(
            t.observe(b(100, 1, 0)), // 100 is below the window
            ReorgOutcome::Duplicate
        );
        assert_eq!(t.head().unwrap().number, 102, "tip preserved");
    }
}
