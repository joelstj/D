//! Response handling (`docs/ENGINE_CONTRACT.md §10`).
//!
//! For each opportunity: confirm every leg's pool was `verified:true` in the
//! request we sent, `net_profit > 0`, and the `block` stamps are the ones we sent.
//! Returns a list of issues (empty = valid) rather than erroring, so a caller can
//! log/count and drop just the bad opportunities.

use alloy_primitives::{B256, U256};
use l2i_core::{DetectRequest, DetectResponse, PoolAddress};
use std::collections::HashSet;

/// A problem found validating an engine response against the request we sent.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ResponseIssue {
    /// `count` did not equal `opportunities.len()`.
    CountMismatch {
        /// The declared count.
        declared: u32,
        /// The actual number of opportunities.
        actual: usize,
    },
    /// A reported opportunity had `net_profit == 0` (must be `> 0`).
    NonPositiveProfit {
        /// Opportunity index.
        index: usize,
    },
    /// A reported opportunity was not `verified`.
    UnverifiedOpportunity {
        /// Opportunity index.
        index: usize,
    },
    /// A leg referenced a pool that was not `verified:true` in the request.
    LegPoolNotVerified {
        /// Opportunity index.
        index: usize,
        /// The offending pool identity.
        pool: PoolAddress,
    },
    /// An opportunity's `block` was not one we sent.
    BlockstampNotInRequest {
        /// Opportunity index.
        index: usize,
    },
}

/// Validate a response against the request that produced it. Empty result = valid.
pub fn validate_response(req: &DetectRequest, resp: &DetectResponse) -> Vec<ResponseIssue> {
    let mut issues = Vec::new();

    if resp.count as usize != resp.opportunities.len() {
        issues.push(ResponseIssue::CountMismatch {
            declared: resp.count,
            actual: resp.opportunities.len(),
        });
    }

    let verified_pools: HashSet<PoolAddress> = req
        .pools
        .iter()
        .filter(|p| p.verified)
        .map(|p| p.address)
        .collect();
    let sent_stamps: HashSet<(u64, u64, B256)> = req
        .pools
        .iter()
        .map(|p| {
            (
                p.blockstamp.chain_id,
                p.blockstamp.number,
                p.blockstamp.block_hash,
            )
        })
        .collect();

    for (index, opp) in resp.opportunities.iter().enumerate() {
        if opp.net_profit.0 == U256::ZERO {
            issues.push(ResponseIssue::NonPositiveProfit { index });
        }
        if !opp.verified {
            issues.push(ResponseIssue::UnverifiedOpportunity { index });
        }
        for leg in &opp.legs {
            if !verified_pools.contains(&leg.pool) {
                issues.push(ResponseIssue::LegPoolNotVerified {
                    index,
                    pool: leg.pool,
                });
            }
        }
        let b = &opp.block;
        if !sent_stamps.contains(&(b.chain_id, b.number, b.hash)) {
            issues.push(ResponseIssue::BlockstampNotInRequest { index });
        }
    }

    issues
}
