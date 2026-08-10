//! Response handling (`docs/ENGINE_CONTRACT.md §10`).
//!
//! For each opportunity: confirm every leg's pool was `verified:true` in the
//! request we sent, `net_profit > 0`, and the `block` stamps are the ones we sent.
//! Returns a list of issues (empty = valid) rather than erroring, so a caller can
//! log/count and drop just the bad opportunities.

use alloy_primitives::{B256, U256};
use l2i_core::{DetectRequest, DetectResponse, Opportunity, PoolAddress};
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

    // Keyed on (chain_id, address), not bare address: ingestion now sends one
    // combined multi-chain request per tick, and several configured L2s are
    // OP-Stack siblings that can share an identical predeploy/pool address. A
    // bare-address key would let a pool verified on chain A rubber-stamp a
    // same-address leg the engine attributes to a different chain B.
    let verified_pools: HashSet<(u64, PoolAddress)> = req
        .pools
        .iter()
        .filter(|p| p.verified)
        .map(|p| (p.blockstamp.chain_id, p.address))
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
            // A leg's authoritative chain is its own token's chain, not the
            // opportunity's (possibly multi-chain) `block` — the pool
            // `leg.pool` refers to lives wherever `leg.token_in` does.
            let key = (leg.token_in.chain_id, leg.pool);
            if !verified_pools.contains(&key) {
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

impl ResponseIssue {
    /// The index of the offending opportunity, or `None` for a whole-response issue
    /// ([`CountMismatch`](ResponseIssue::CountMismatch)) not tied to one opportunity.
    pub fn opportunity_index(&self) -> Option<usize> {
        match self {
            ResponseIssue::CountMismatch { .. } => None,
            ResponseIssue::NonPositiveProfit { index }
            | ResponseIssue::UnverifiedOpportunity { index }
            | ResponseIssue::LegPoolNotVerified { index, .. }
            | ResponseIssue::BlockstampNotInRequest { index } => Some(*index),
        }
    }
}

/// Filter an engine response down to only the opportunities that pass
/// [`validate_response`] against `req`, returning the cleaned response plus the issues
/// found (for logging/metrics). An invalid opportunity — an unverified leg,
/// `net_profit == 0`, an unverified flag, or a blockstamp we never sent — is
/// **dropped**, never forwarded, so the dashboard never receives a phantom the
/// ingestion layer already knows is bad (`verified` honesty). The returned `count`
/// equals the kept length; `timing` is relayed verbatim.
pub fn retain_valid(
    req: &DetectRequest,
    resp: &DetectResponse,
) -> (DetectResponse, Vec<ResponseIssue>) {
    let issues = validate_response(req, resp);
    let bad: HashSet<usize> = issues
        .iter()
        .filter_map(ResponseIssue::opportunity_index)
        .collect();
    let opportunities: Vec<Opportunity> = resp
        .opportunities
        .iter()
        .enumerate()
        .filter(|(i, _)| !bad.contains(i))
        .map(|(_, o)| o.clone())
        .collect();
    let cleaned = DetectResponse {
        count: opportunities.len() as u32,
        opportunities,
        timing: resp.timing.clone(),
    };
    (cleaned, issues)
}

#[cfg(test)]
mod tests {
    use super::*;
    use alloy_primitives::{Address, B256};
    use l2i_core::{
        Block, Blockstamp, DecU256, Leg, Pool, PoolAddress, PoolKind, Risk, Token, V2State,
    };

    fn token(sym: &str, b: u8) -> Token {
        Token::with_symbol(42161, Address::from([b; 20]), 18, sym)
    }

    /// A block-stamped verified V2 pool at `(number, hash)`.
    fn pool(addr: u8, number: u64, hash: B256, verified: bool) -> Pool {
        Pool {
            address: PoolAddress::Contract(Address::from([addr; 20])),
            kind: PoolKind::V2,
            fee_pips: 3000,
            verified,
            token0: token("A", 1),
            token1: token("B", 2),
            blockstamp: Blockstamp {
                chain_id: 42161,
                number,
                block_hash: hash,
                timestamp: number,
            },
            v2: Some(V2State {
                reserve0: DecU256(U256::from(1_000u64)),
                reserve1: DecU256(U256::from(2_000u64)),
            }),
            v3: None,
        }
    }

    /// An opportunity routing through `leg_pool`, with the given profit and block.
    fn opp(
        leg_pool: u8,
        net_profit: u64,
        verified: bool,
        block_number: u64,
        hash: B256,
    ) -> Opportunity {
        Opportunity {
            strategy: "two_hop".into(),
            numeraire: token("A", 1),
            input_amount: DecU256(U256::from(100u64)),
            output_amount: DecU256(U256::from(110u64)),
            gross_profit: DecU256(U256::from(net_profit + 5)),
            gas_cost: DecU256(U256::from(3u64)),
            bridge_cost: DecU256(U256::ZERO),
            net_profit: DecU256(U256::from(net_profit)),
            profit_bps: 10.0,
            expected_net: DecU256(U256::from(net_profit)),
            score: 1.0,
            hops: 2,
            chain_ids: vec![42161],
            is_cross_chain: false,
            settle_seconds: 0,
            verified,
            block: Block {
                chain_id: 42161,
                number: block_number,
                hash,
                timestamp: block_number,
            },
            risk: Risk {
                success_probability: 0.9,
                capture_ratio: 0.8,
                frontrun_risk: 0.1,
                notes: vec![],
            },
            legs: vec![Leg {
                pool: PoolAddress::Contract(Address::from([leg_pool; 20])),
                token_in: token("A", 1),
                token_out: token("B", 2),
                amount_in: DecU256(U256::from(100u64)),
                amount_out: DecU256(U256::from(110u64)),
            }],
        }
    }

    /// Like [`pool`] but on an explicit chain (chain-scoped verification tests).
    fn pool_on_chain(chain_id: u64, addr: u8, number: u64, hash: B256, verified: bool) -> Pool {
        Pool {
            address: PoolAddress::Contract(Address::from([addr; 20])),
            kind: PoolKind::V2,
            fee_pips: 3000,
            verified,
            token0: Token::with_symbol(chain_id, Address::from([1; 20]), 18, "A"),
            token1: Token::with_symbol(chain_id, Address::from([2; 20]), 18, "B"),
            blockstamp: Blockstamp {
                chain_id,
                number,
                block_hash: hash,
                timestamp: number,
            },
            v2: Some(V2State {
                reserve0: DecU256(U256::from(1_000u64)),
                reserve1: DecU256(U256::from(2_000u64)),
            }),
            v3: None,
        }
    }

    /// Like [`opp`] but the leg's tokens — and therefore its authoritative chain
    /// — live on an explicit chain, independent of any other pool/chain that
    /// might appear in the same request (chain-scoped verification tests).
    fn opp_on_chain(
        chain_id: u64,
        leg_pool: u8,
        net_profit: u64,
        verified: bool,
        block_number: u64,
        hash: B256,
    ) -> Opportunity {
        Opportunity {
            strategy: "two_hop".into(),
            numeraire: Token::with_symbol(chain_id, Address::from([1; 20]), 18, "A"),
            input_amount: DecU256(U256::from(100u64)),
            output_amount: DecU256(U256::from(110u64)),
            gross_profit: DecU256(U256::from(net_profit + 5)),
            gas_cost: DecU256(U256::from(3u64)),
            bridge_cost: DecU256(U256::ZERO),
            net_profit: DecU256(U256::from(net_profit)),
            profit_bps: 10.0,
            expected_net: DecU256(U256::from(net_profit)),
            score: 1.0,
            hops: 2,
            chain_ids: vec![chain_id],
            is_cross_chain: false,
            settle_seconds: 0,
            verified,
            block: Block {
                chain_id,
                number: block_number,
                hash,
                timestamp: block_number,
            },
            risk: Risk {
                success_probability: 0.9,
                capture_ratio: 0.8,
                frontrun_risk: 0.1,
                notes: vec![],
            },
            legs: vec![Leg {
                pool: PoolAddress::Contract(Address::from([leg_pool; 20])),
                token_in: Token::with_symbol(chain_id, Address::from([1; 20]), 18, "A"),
                token_out: Token::with_symbol(chain_id, Address::from([2; 20]), 18, "B"),
                amount_in: DecU256(U256::from(100u64)),
                amount_out: DecU256(U256::from(110u64)),
            }],
        }
    }

    #[test]
    fn retain_valid_drops_only_the_invalid_opportunity() {
        let hash = B256::from([7; 32]);
        // Request: one verified pool 0xAA at (block 500, hash).
        let req = DetectRequest {
            top_n: 10,
            max_hops: 4,
            incremental: false,
            chains: vec![],
            pools: vec![pool(0xAA, 500, hash, true)],
            cross_chain: None,
        };
        // Two opportunities: a VALID one (verified, profit>0, leg pool verified & in
        // request, blockstamp we sent) and an INVALID one (routes through pool 0xBB,
        // which was never verified in the request → phantom).
        let good = opp(0xAA, 42, true, 500, hash);
        let bad = opp(0xBB, 42, true, 500, hash);
        let resp = DetectResponse {
            count: 2,
            opportunities: vec![good.clone(), bad],
            timing: None,
        };

        let (clean, issues) = retain_valid(&req, &resp);
        assert!(
            !issues.is_empty(),
            "the phantom opportunity must be reported as an issue"
        );
        assert_eq!(clean.count, 1, "count reflects the kept opportunities");
        assert_eq!(clean.opportunities.len(), 1);
        assert_eq!(
            clean.opportunities[0], good,
            "only the valid opportunity survives; the phantom is dropped"
        );
    }

    #[test]
    fn retain_valid_drops_zero_profit_and_stale_blockstamp() {
        let hash = B256::from([7; 32]);
        let stale = B256::from([9; 32]);
        let req = DetectRequest {
            top_n: 10,
            max_hops: 4,
            incremental: false,
            chains: vec![],
            pools: vec![pool(0xAA, 500, hash, true)],
            cross_chain: None,
        };
        let good = opp(0xAA, 42, true, 500, hash);
        let zero_profit = opp(0xAA, 0, true, 500, hash); // net_profit == 0 → invalid
        let stale_stamp = opp(0xAA, 42, true, 500, stale); // blockstamp we never sent
        let resp = DetectResponse {
            count: 3,
            opportunities: vec![good.clone(), zero_profit, stale_stamp],
            timing: None,
        };

        let (clean, _issues) = retain_valid(&req, &resp);
        assert_eq!(
            clean.opportunities,
            vec![good],
            "both invalid opportunities dropped"
        );
    }

    #[test]
    fn retain_valid_keeps_a_fully_valid_response_untouched() {
        let hash = B256::from([7; 32]);
        let req = DetectRequest {
            top_n: 10,
            max_hops: 4,
            incremental: false,
            chains: vec![],
            pools: vec![pool(0xAA, 500, hash, true)],
            cross_chain: None,
        };
        let good = opp(0xAA, 42, true, 500, hash);
        let resp = DetectResponse {
            count: 1,
            opportunities: vec![good],
            timing: None,
        };
        let (clean, issues) = retain_valid(&req, &resp);
        assert!(issues.is_empty(), "a valid response has no issues");
        assert_eq!(clean, resp, "a valid response is passed through unchanged");
    }

    #[test]
    fn leg_pool_verification_is_scoped_to_the_legs_own_chain() {
        // Two chains can share an identical pool/predeploy address (several
        // configured L2s are OP-Stack siblings). A pool verified on chain 8453
        // must not rubber-stamp a same-address leg the engine attributes to a
        // DIFFERENT chain (10), where that address was never verified.
        let shared_addr = 0xAA;
        let hash_base = B256::from([7; 32]);
        let hash_op = B256::from([8; 32]);
        let req = DetectRequest {
            top_n: 10,
            max_hops: 4,
            incremental: false,
            chains: vec![],
            pools: vec![
                pool_on_chain(8453, shared_addr, 500, hash_base, true), // verified, Base
                pool_on_chain(10, shared_addr, 900, hash_op, false),    // NOT verified, Optimism
            ],
            cross_chain: None,
        };
        let bad = opp_on_chain(10, shared_addr, 42, true, 900, hash_op);
        let resp = DetectResponse {
            count: 1,
            opportunities: vec![bad],
            timing: None,
        };

        let issues = validate_response(&req, &resp);
        assert_eq!(
            issues,
            vec![ResponseIssue::LegPoolNotVerified {
                index: 0,
                pool: PoolAddress::Contract(Address::from([shared_addr; 20])),
            }],
            "chain 8453's verification of this address must not leak into chain 10: {issues:?}"
        );
    }

    #[test]
    fn leg_pool_verification_still_passes_on_the_same_chain() {
        // Control case: same address, same chain as the verified pool — must
        // still validate normally (no false positive from the chain-scoping fix).
        let addr = 0xAA;
        let hash = B256::from([7; 32]);
        let req = DetectRequest {
            top_n: 10,
            max_hops: 4,
            incremental: false,
            chains: vec![],
            pools: vec![pool_on_chain(8453, addr, 500, hash, true)],
            cross_chain: None,
        };
        let good = opp_on_chain(8453, addr, 42, true, 500, hash);
        let resp = DetectResponse {
            count: 1,
            opportunities: vec![good],
            timing: None,
        };
        assert!(validate_response(&req, &resp).is_empty());
    }

    #[test]
    fn retain_valid_drops_a_cross_chain_address_collision_phantom() {
        // End-to-end: retain_valid must actually drop (not just flag) an
        // opportunity whose leg exploits a same-address pool verified on a
        // different chain.
        let shared_addr = 0xAA;
        let hash_base = B256::from([7; 32]);
        let hash_op = B256::from([8; 32]);
        let req = DetectRequest {
            top_n: 10,
            max_hops: 4,
            incremental: false,
            chains: vec![],
            pools: vec![
                pool_on_chain(8453, shared_addr, 500, hash_base, true),
                pool_on_chain(10, shared_addr, 900, hash_op, false),
            ],
            cross_chain: None,
        };
        let good = opp_on_chain(8453, shared_addr, 42, true, 500, hash_base);
        let phantom = opp_on_chain(10, shared_addr, 42, true, 900, hash_op);
        let resp = DetectResponse {
            count: 2,
            opportunities: vec![good.clone(), phantom],
            timing: None,
        };

        let (clean, issues) = retain_valid(&req, &resp);
        assert!(!issues.is_empty());
        assert_eq!(
            clean.opportunities,
            vec![good],
            "only the legitimately-verified opportunity survives"
        );
    }

    /// A genuinely cross-chain, two-leg opportunity: leg 0's pool lives on the
    /// source chain, leg 1's on the destination chain. Every prior test in this
    /// file uses `is_cross_chain: false` with a single leg — this exercises the
    /// shape `docs/notes-cross-chain-flash-loan-gaps.md` (ingestion §) actually
    /// cares about: does per-leg, chain-scoped verification (the fix for I2)
    /// generalize to a real multi-chain route, or was it only ever proven
    /// against two *separate* single-chain opportunities colliding on address?
    fn cross_chain_opp(
        src_chain: u64,
        src_pool: u8,
        dst_chain: u64,
        dst_pool: u8,
        net_profit: u64,
        src_block: (u64, B256),
    ) -> Opportunity {
        let src_token = |b: u8| Token::with_symbol(src_chain, Address::from([b; 20]), 18, "A");
        let dst_token = |b: u8| Token::with_symbol(dst_chain, Address::from([b; 20]), 18, "B");
        Opportunity {
            strategy: "cross_chain_two_hop".into(),
            numeraire: src_token(1),
            input_amount: DecU256(U256::from(100u64)),
            output_amount: DecU256(U256::from(110u64)),
            gross_profit: DecU256(U256::from(net_profit + 5)),
            gas_cost: DecU256(U256::from(3u64)),
            bridge_cost: DecU256(U256::from(2u64)),
            net_profit: DecU256(U256::from(net_profit)),
            profit_bps: 10.0,
            expected_net: DecU256(U256::from(net_profit)),
            score: 1.0,
            hops: 2,
            chain_ids: vec![src_chain, dst_chain],
            is_cross_chain: true,
            settle_seconds: 600,
            verified: true,
            block: Block {
                chain_id: src_chain,
                number: src_block.0,
                hash: src_block.1,
                timestamp: src_block.0,
            },
            risk: Risk {
                success_probability: 0.7,
                capture_ratio: 0.6,
                frontrun_risk: 0.1,
                notes: vec![],
            },
            legs: vec![
                Leg {
                    pool: PoolAddress::Contract(Address::from([src_pool; 20])),
                    token_in: src_token(1),
                    token_out: src_token(2),
                    amount_in: DecU256(U256::from(100u64)),
                    amount_out: DecU256(U256::from(105u64)),
                },
                Leg {
                    pool: PoolAddress::Contract(Address::from([dst_pool; 20])),
                    token_in: dst_token(1),
                    token_out: dst_token(2),
                    amount_in: DecU256(U256::from(105u64)),
                    amount_out: DecU256(U256::from(110u64)),
                },
            ],
        }
    }

    #[test]
    fn cross_chain_two_leg_opportunity_survives_when_both_legs_are_verified_on_their_own_chain() {
        let src_hash = B256::from([7; 32]);
        let dst_hash = B256::from([8; 32]);
        let req = DetectRequest {
            top_n: 10,
            max_hops: 4,
            incremental: false,
            chains: vec![],
            pools: vec![
                pool_on_chain(8453, 0xAA, 500, src_hash, true), // source leg, verified
                pool_on_chain(10, 0xBB, 900, dst_hash, true),   // dest leg, verified
            ],
            cross_chain: None,
        };
        let opp = cross_chain_opp(8453, 0xAA, 10, 0xBB, 42, (500, src_hash));
        let resp = DetectResponse {
            count: 1,
            opportunities: vec![opp.clone()],
            timing: None,
        };

        let issues = validate_response(&req, &resp);
        assert!(
            issues.is_empty(),
            "both legs verified on their own chain: {issues:?}"
        );
        let (clean, _) = retain_valid(&req, &resp);
        assert_eq!(clean.opportunities, vec![opp]);
    }

    #[test]
    fn cross_chain_opportunity_is_dropped_whole_when_only_the_destination_leg_is_unverified() {
        // The source leg's pool IS verified (and, to prove the check is truly
        // per-leg and not just "was anything in this request verified", a
        // same-address pool is ALSO verified — but on a third, unrelated
        // chain, so it must not leak into the destination chain's check any
        // more than I2's single-leg regression proved for same-chain opps).
        // A real detector bug here — e.g. validating a cross-chain opp's legs
        // against a single collapsed "any chain" set instead of per-leg — is
        // exactly the "silently starved of data" shape this session's audit
        // brief calls out: it would either wrongly drop legitimate cross-chain
        // opportunities or, worse, wrongly admit ones whose destination leg was
        // never actually verified.
        let src_hash = B256::from([7; 32]);
        let unrelated_hash = B256::from([9; 32]);
        let req = DetectRequest {
            top_n: 10,
            max_hops: 4,
            incremental: false,
            chains: vec![],
            pools: vec![
                pool_on_chain(8453, 0xAA, 500, src_hash, true), // source leg, verified
                pool_on_chain(10, 0xBB, 900, B256::from([8; 32]), false), // dest leg, NOT verified
                pool_on_chain(42161, 0xBB, 300, unrelated_hash, true), // same addr as dest leg, different chain, verified
            ],
            cross_chain: None,
        };
        let opp = cross_chain_opp(8453, 0xAA, 10, 0xBB, 42, (500, src_hash));
        let resp = DetectResponse {
            count: 1,
            opportunities: vec![opp],
            timing: None,
        };

        let issues = validate_response(&req, &resp);
        assert_eq!(
            issues,
            vec![ResponseIssue::LegPoolNotVerified {
                index: 0,
                pool: PoolAddress::Contract(Address::from([0xBB; 20])),
            }],
            "destination leg must be checked against its OWN chain's verified set: {issues:?}"
        );

        // retain_valid must drop the WHOLE opportunity — not forward it with
        // its unverified destination leg intact.
        let (clean, _) = retain_valid(&req, &resp);
        assert!(
            clean.opportunities.is_empty(),
            "a cross-chain opportunity with one unverified leg must not be forwarded at all"
        );
    }
}
