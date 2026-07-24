//! `cross_chain` wiring (`docs/ENGINE_CONTRACT.md §8`).
//!
//! Represent only assets with a genuine same-asset representation on **≥2** chains;
//! prune bridges and pairs that reference a dropped asset. We never invent routes.

use l2i_core::CrossChain;
use std::collections::HashSet;

/// Filter a config-derived [`CrossChain`] down to the assets/bridges/pairs the
/// engine can actually use: assets present on ≥2 chains, and bridges/pairs whose
/// symbols all survive.
pub fn filter_cross_chain(cc: CrossChain) -> CrossChain {
    let assets: Vec<_> = cc
        .assets
        .into_iter()
        .filter(|a| a.representations.len() >= 2)
        .collect();
    let kept: HashSet<String> = assets.iter().map(|a| a.symbol.clone()).collect();

    let bridges = cc
        .bridges
        .into_iter()
        .filter(|b| kept.contains(&b.symbol))
        .collect();
    let pairs = cc
        .pairs
        .into_iter()
        .filter(|[a, n]| kept.contains(a) && kept.contains(n))
        .collect();

    CrossChain {
        assets,
        bridges,
        pairs,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use l2i_core::{Asset, Bridge, Representation, Token};

    fn rep(chain_id: u64) -> Representation {
        Representation {
            token: Token::bare(chain_id, alloy_primitives::Address::ZERO, 18),
            native: true,
            bridgeable: true,
        }
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
        let out = filter_cross_chain(cc);
        assert_eq!(out.assets.len(), 1);
        assert_eq!(out.assets[0].symbol, "WETH");
        assert_eq!(out.bridges.len(), 1, "USDC bridge pruned");
        // The WETH/USDC pair is pruned (USDC dropped); WETH/WETH survives.
        assert_eq!(out.pairs, vec![["WETH".to_string(), "WETH".to_string()]]);
    }
}
