//! `cross_chain` wiring (`docs/ENGINE_CONTRACT.md §8`).
//!
//! Represent only assets with a genuine same-asset representation on **≥2**
//! *enabled* chains **and** a real configured bridge between them; prune
//! representations on a chain we don't run, and bridges/pairs that reference a
//! dropped asset. We never invent routes.

use l2i_core::{Asset, CrossChain};
use std::collections::HashSet;

/// Before/after counts from [`filter_cross_chain`] — how much of the configured
/// cross-chain wiring survived down to what the engine can actually use. Pure
/// data (no I/O), so a caller can both log it (`tracing::warn!`/`--check-config`)
/// and assert on it in a test, mirroring `pipeline.rs::hold_back_reason()`'s
/// reason-returning idiom instead of only emitting an untestable side effect.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct CrossChainCounts {
    /// Assets handed to the filter.
    pub assets_in: usize,
    /// Assets that survived (≥2 enabled-chain representations + a real bridge).
    pub assets_out: usize,
    /// Bridges handed to the filter.
    pub bridges_in: usize,
    /// Bridges that survived (reference a surviving asset's symbol).
    pub bridges_out: usize,
    /// Pairs handed to the filter.
    pub pairs_in: usize,
    /// Pairs that survived (both symbols reference a surviving asset).
    pub pairs_out: usize,
}

/// Filter a config-derived [`CrossChain`] down to the assets/bridges/pairs the
/// engine can actually use, returning it alongside before/after counts (for
/// logging / `--check-config` — see [`CrossChainCounts`]).
///
/// An asset is kept only when, after dropping any representation whose chain
/// isn't in `enabled_chain_ids` (nothing will ever send pool data for a chain we
/// don't run, so that representation isn't usable), it still has **both**:
/// - a genuine same-asset representation on ≥2 chains, and
/// - at least one `bridges[]` entry referencing its symbol,
///
/// matching `docs/ENGINE_CONTRACT.md §8`: "a genuine same-asset representation on
/// ≥2 of our chains **and a real bridge between them**." We never invent a route.
/// Bridges/pairs are then pruned to the symbols that survive.
pub fn filter_cross_chain(
    cc: CrossChain,
    enabled_chain_ids: &HashSet<u64>,
) -> (CrossChain, CrossChainCounts) {
    let assets_in = cc.assets.len();
    let bridges_in = cc.bridges.len();
    let pairs_in = cc.pairs.len();

    // Whether a real bridge route exists for a symbol is checked against the
    // full, pre-filter bridge list — it doesn't depend on which individual
    // representations happen to survive the chain-enabled prune below.
    let bridge_symbols: HashSet<&str> = cc.bridges.iter().map(|b| b.symbol.as_str()).collect();

    let assets: Vec<Asset> = cc
        .assets
        .into_iter()
        .filter_map(|a| {
            let Asset {
                symbol,
                representations,
            } = a;
            // Drop representations on a chain we don't have enabled — a
            // representation nothing will ever send pool data for is not
            // usable, and counting it toward the "≥2 chains" bar below would be
            // misleading.
            let representations: Vec<_> = representations
                .into_iter()
                .filter(|r| {
                    let keep = enabled_chain_ids.contains(&r.token.chain_id);
                    if !keep {
                        tracing::debug!(
                            symbol = %symbol,
                            chain_id = r.token.chain_id,
                            "cross-chain representation dropped: chain not enabled"
                        );
                    }
                    keep
                })
                .collect();
            let has_real_bridge = bridge_symbols.contains(symbol.as_str());
            if representations.len() >= 2 && has_real_bridge {
                Some(Asset {
                    symbol,
                    representations,
                })
            } else {
                None
            }
        })
        .collect();
    let kept: HashSet<String> = assets.iter().map(|a| a.symbol.clone()).collect();

    let bridges: Vec<_> = cc
        .bridges
        .into_iter()
        .filter(|b| kept.contains(&b.symbol))
        .collect();
    let pairs: Vec<_> = cc
        .pairs
        .into_iter()
        .filter(|[a, n]| kept.contains(a) && kept.contains(n))
        .collect();

    let counts = CrossChainCounts {
        assets_in,
        assets_out: assets.len(),
        bridges_in,
        bridges_out: bridges.len(),
        pairs_in,
        pairs_out: pairs.len(),
    };

    (
        CrossChain {
            assets,
            bridges,
            pairs,
        },
        counts,
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use l2i_core::{Bridge, Representation, Token};

    fn rep(chain_id: u64) -> Representation {
        Representation {
            token: Token::bare(chain_id, alloy_primitives::Address::ZERO, 18),
            native: true,
            bridgeable: true,
        }
    }

    fn enabled(ids: &[u64]) -> HashSet<u64> {
        ids.iter().copied().collect()
    }

    #[test]
    fn drops_single_chain_assets_and_dangling_bridges() {
        let cc = CrossChain {
            assets: vec![
                Asset {
                    symbol: "WETH".into(),
                    representations: vec![rep(1), rep(2)],
                },
                // USDC only on one chain → dropped.
                Asset {
                    symbol: "USDC".into(),
                    representations: vec![rep(1)],
                },
            ],
            bridges: vec![
                Bridge {
                    symbol: "WETH".into(),
                    from_chain: 1,
                    to_chain: 2,
                    fee_bps: 10.0,
                    fixed_fee: 0,
                    settle_seconds: 600,
                },
                Bridge {
                    symbol: "USDC".into(),
                    from_chain: 1,
                    to_chain: 2,
                    fee_bps: 10.0,
                    fixed_fee: 0,
                    settle_seconds: 600,
                },
            ],
            pairs: vec![
                ["WETH".into(), "USDC".into()],
                ["WETH".into(), "WETH".into()],
            ],
        };
        let (out, counts) = filter_cross_chain(cc, &enabled(&[1, 2]));
        assert_eq!(out.assets.len(), 1);
        assert_eq!(out.assets[0].symbol, "WETH");
        assert_eq!(out.bridges.len(), 1, "USDC bridge pruned");
        // The WETH/USDC pair is pruned (USDC dropped); WETH/WETH survives.
        assert_eq!(out.pairs, vec![["WETH".to_string(), "WETH".to_string()]]);

        assert_eq!(
            counts,
            CrossChainCounts {
                assets_in: 2,
                assets_out: 1,
                bridges_in: 2,
                bridges_out: 1,
                pairs_in: 2,
                pairs_out: 1,
            }
        );
    }

    #[test]
    fn drops_asset_with_representations_but_no_matching_bridge() {
        // The specific missing case from the audit: 2 representations clears
        // the old, lone threshold, but zero `bridges[]` entries reference the
        // symbol at all — the documented "and a real bridge between them"
        // invariant must still drop it.
        let cc = CrossChain {
            assets: vec![Asset {
                symbol: "WETH".into(),
                representations: vec![rep(1), rep(2)],
            }],
            bridges: vec![],
            pairs: vec![["WETH".into(), "WETH".into()]],
        };
        let (out, counts) = filter_cross_chain(cc, &enabled(&[1, 2]));
        assert!(
            out.assets.is_empty(),
            "2 representations alone is not enough without a real bridge"
        );
        assert!(out.pairs.is_empty(), "pair pruned along with its asset");
        assert_eq!(counts.assets_in, 1);
        assert_eq!(counts.assets_out, 0);
    }

    #[test]
    fn drops_asset_whose_only_bridge_is_for_a_different_symbol() {
        // A bridge is configured, but not for THIS asset — must not count as
        // "a real bridge between them" for WETH.
        let cc = CrossChain {
            assets: vec![Asset {
                symbol: "WETH".into(),
                representations: vec![rep(1), rep(2)],
            }],
            bridges: vec![Bridge {
                symbol: "USDC".into(),
                from_chain: 1,
                to_chain: 2,
                fee_bps: 10.0,
                fixed_fee: 0,
                settle_seconds: 600,
            }],
            pairs: vec![],
        };
        let (out, _counts) = filter_cross_chain(cc, &enabled(&[1, 2]));
        assert!(out.assets.is_empty());
    }

    #[test]
    fn drops_representations_on_disabled_chains() {
        // WETH has 3 representations (chains 1, 2, 3) and a real bridge, but
        // only chains 1 and 2 are enabled — chain 3's representation must not
        // survive, even though it parsed fine.
        let cc = CrossChain {
            assets: vec![Asset {
                symbol: "WETH".into(),
                representations: vec![rep(1), rep(2), rep(3)],
            }],
            bridges: vec![Bridge {
                symbol: "WETH".into(),
                from_chain: 1,
                to_chain: 2,
                fee_bps: 10.0,
                fixed_fee: 0,
                settle_seconds: 600,
            }],
            pairs: vec![],
        };
        let (out, _counts) = filter_cross_chain(cc, &enabled(&[1, 2]));
        assert_eq!(out.assets.len(), 1, "still >=2 enabled reps + a bridge");
        assert_eq!(out.assets[0].representations.len(), 2);
        assert!(
            out.assets[0]
                .representations
                .iter()
                .all(|r| r.token.chain_id != 3),
            "chain 3's representation must be dropped: it isn't enabled"
        );
    }

    #[test]
    fn disabled_chain_representation_can_drop_an_asset_below_threshold() {
        // WETH has 2 representations (chains 1 and 3) and a real bridge, but
        // chain 3 isn't enabled — after pruning, only 1 representation is left,
        // below the >=2 threshold, so the whole asset is dropped even though it
        // had a matching bridge and 2 representations pre-filter.
        let cc = CrossChain {
            assets: vec![Asset {
                symbol: "WETH".into(),
                representations: vec![rep(1), rep(3)],
            }],
            bridges: vec![Bridge {
                symbol: "WETH".into(),
                from_chain: 1,
                to_chain: 3,
                fee_bps: 10.0,
                fixed_fee: 0,
                settle_seconds: 600,
            }],
            pairs: vec![],
        };
        let (out, counts) = filter_cross_chain(cc, &enabled(&[1, 2]));
        assert!(out.assets.is_empty());
        assert_eq!(counts.assets_out, 0);
    }
}
