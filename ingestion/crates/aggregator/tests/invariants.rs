//! Snapshot invariants (M8 Tier-A, `docs/ARCHITECTURE.md §9` item 2):
//! one block per chain per request; incremental sends only changed pools; the
//! first request is `incremental:false`.

use alloy_primitives::{Address, B256, U256};
use l2i_aggregator::request::{
    build_detect_request, ChainSnapshot, IncrementalPolicy, RequestConfig,
};
use l2i_aggregator::snapshot::{re_stamp, IncrementalTracker};
use l2i_core::{Blockstamp, ChainContext, DecU256, Pool, PoolAddress, PoolKind, Token, V2State};
use proptest::prelude::*;
use std::collections::BTreeMap;

fn pool(chain_id: u64, addr_byte: u8, reserve: u128, number: u64) -> Pool {
    let a = Address::from([addr_byte; 20]);
    Pool {
        address: PoolAddress::Contract(a),
        kind: PoolKind::V2,
        fee_pips: 3000,
        verified: true,
        token0: Token::with_symbol(chain_id, Address::from([1u8; 20]), 18, "A"),
        token1: Token::with_symbol(chain_id, Address::from([2u8; 20]), 6, "B"),
        blockstamp: Blockstamp {
            chain_id,
            number,
            block_hash: B256::from([number as u8; 32]),
            timestamp: number,
        },
        v2: Some(V2State {
            reserve0: DecU256(U256::from(reserve)),
            reserve1: DecU256(U256::from(reserve + 1)),
        }),
        v3: None,
    }
}

fn head(chain_id: u64, number: u64) -> Blockstamp {
    Blockstamp {
        chain_id,
        number,
        block_hash: B256::from([0xAB; 32]),
        timestamp: number,
    }
}

proptest! {
    // Invariant 1: after snapshotting, every pool of a chain shares one blockstamp.
    #[test]
    fn one_block_per_chain(reserves in proptest::collection::vec(1u128..1_000_000, 1..20), number in 1u64..1_000_000) {
        let pools: Vec<Pool> = reserves.iter().enumerate()
            .map(|(i, &r)| pool(42161, i as u8, r, (number % 100) + i as u64)).collect();
        let h = head(42161, number);
        let stamped = re_stamp(&h, pools);
        for p in &stamped {
            prop_assert_eq!(&p.blockstamp, &h);
        }
    }

    // Invariant 2: incremental resends only changed pools.
    #[test]
    fn incremental_sends_only_changed(
        reserves in proptest::collection::vec(1u128..1_000_000, 1..15),
        change_idx in 0usize..15,
        new_reserve in 1u128..1_000_000,
    ) {
        let pools: Vec<Pool> = reserves.iter().enumerate().map(|(i, &r)| pool(1, i as u8, r, 1)).collect();
        let mut tracker = IncrementalTracker::new();

        // First pass records everything.
        let first = tracker.changed(&pools);
        prop_assert_eq!(first.len(), pools.len());

        // Second pass, unchanged → nothing resent.
        let second = tracker.changed(&pools);
        prop_assert!(second.is_empty(), "unchanged pools must not be resent");

        // Change one pool's reserve → only it is resent (if the value differs).
        if change_idx < pools.len() {
            let mut changed_pools = pools.clone();
            if let Some(v2) = &mut changed_pools[change_idx].v2 {
                let old = v2.reserve0.0;
                v2.reserve0 = DecU256(U256::from(new_reserve));
                let third = tracker.changed(&changed_pools);
                if U256::from(new_reserve) != old {
                    prop_assert_eq!(third.len(), 1);
                    prop_assert_eq!(third[0].address, changed_pools[change_idx].address);
                } else {
                    prop_assert!(third.is_empty());
                }
            }
        }
    }

    // Invariant 3: the first request of a session is incremental:false.
    #[test]
    fn first_request_full(incremental_after in any::<bool>(), rounds in 1u32..10) {
        let mut policy = IncrementalPolicy::new(incremental_after);
        let ctx = ChainContext {
            chain_id: 1, gas_price_wei: 1, l1_data_fee_wei: 0, base_gas: 1, per_hop_gas: 1,
            gas_safety_multiplier: 1.0, min_profit_bps: 1.0, native_price_in: BTreeMap::new(), hubs: vec![],
        };
        for round in 0..rounds {
            let inc = policy.next_incremental();
            let req = build_detect_request(
                vec![ChainSnapshot { context: ctx.clone(), pools: vec![pool(1, 0, 100, 1)] }],
                inc, None, RequestConfig::default(),
            );
            if round == 0 {
                prop_assert!(!req.incremental, "first request must be full");
            } else {
                prop_assert_eq!(req.incremental, incremental_after);
            }
            // Always exactly one ChainContext per chain in the request.
            prop_assert_eq!(req.chains.len(), 1);
        }
    }
}
