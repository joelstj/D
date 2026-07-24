//! Build the engine's `cross_chain` block from config — the conversion the pipeline
//! was missing (it hardcoded `None`, dark-ing the product's entire reason to exist).
//!
//! Representations with an unparseable address are skipped, so a config full of
//! placeholders yields `None` rather than garbage; the result is then pruned by
//! [`filter_cross_chain`] to assets present on ≥2 chains (and the bridges/pairs whose
//! symbols survive). We never invent a route.

use alloy_primitives::Address;
use l2i_aggregator::filter_cross_chain;
use l2i_config::Config;
use l2i_core::{Asset, Bridge, CrossChain, Representation, Token};
use std::str::FromStr;

/// Convert + filter the config's cross-chain wiring into an engine [`CrossChain`].
/// Returns `None` when cross-chain is absent, disabled, or has nothing usable after
/// filtering (e.g. every representation address is still a placeholder).
pub fn build_cross_chain(config: &Config) -> Option<CrossChain> {
    let cc = config.cross_chain.as_ref()?;
    if !cc.enabled {
        return None;
    }
    let assets = cc
        .assets
        .iter()
        .map(|a| Asset {
            symbol: a.symbol.clone(),
            representations: a
                .representations
                .iter()
                .filter_map(|r| {
                    let addr = Address::from_str(r.address.trim()).ok()?;
                    Some(Representation {
                        token: Token::bare(r.chain_id, addr, r.decimals),
                        native: r.native,
                        bridgeable: r.bridgeable,
                    })
                })
                .collect(),
        })
        .collect();
    let bridges = cc
        .bridges
        .iter()
        .map(|b| Bridge {
            symbol: b.symbol.clone(),
            from_chain: b.from_chain,
            to_chain: b.to_chain,
            fee_bps: b.fee_bps,
            fixed_fee: b.fixed_fee,
            settle_seconds: b.settle_seconds,
        })
        .collect();
    let filtered = filter_cross_chain(CrossChain {
        assets,
        bridges,
        pairs: cc.pairs.clone(),
    });
    if filtered.assets.is_empty() {
        None
    } else {
        Some(filtered)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use l2i_config::{Config, CrossChainAsset, CrossChainBridge, CrossChainConfig, CrossChainRep};

    fn cfg_with(cc: CrossChainConfig) -> Config {
        // Load the example for its non-cross-chain scaffolding, then swap in `cc`.
        let mut c = Config::load(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../config/config.example.toml"
        ))
        .unwrap();
        c.cross_chain = Some(cc);
        c
    }

    fn rep(chain_id: u64, addr: &str, dec: u8) -> CrossChainRep {
        CrossChainRep {
            chain_id,
            address: addr.to_string(),
            decimals: dec,
            native: true,
            bridgeable: true,
        }
    }

    #[test]
    fn builds_from_real_addresses_and_prunes_single_chain_assets() {
        // WETH on 2 chains (kept), USDC on only 1 (pruned). Real addresses.
        let cc = CrossChainConfig {
            enabled: true,
            pairs: vec![["WETH".into(), "USDC".into()]],
            assets: vec![
                CrossChainAsset {
                    symbol: "WETH".into(),
                    representations: vec![
                        rep(42161, "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1", 18),
                        rep(8453, "0x4200000000000000000000000000000000000006", 18),
                    ],
                },
                CrossChainAsset {
                    symbol: "USDC".into(),
                    representations: vec![rep(
                        42161,
                        "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
                        6,
                    )],
                },
            ],
            bridges: vec![CrossChainBridge {
                symbol: "WETH".into(),
                from_chain: 42161,
                to_chain: 8453,
                fee_bps: 10.0,
                fixed_fee: 0,
                settle_seconds: 600,
            }],
        };
        let out = build_cross_chain(&cfg_with(cc)).expect("usable cross-chain");
        // WETH survives (2 chains); USDC pruned (1 chain).
        assert_eq!(out.assets.len(), 1);
        assert_eq!(out.assets[0].symbol, "WETH");
        assert_eq!(out.assets[0].representations.len(), 2);
        assert_eq!(out.bridges.len(), 1, "bridge on a surviving symbol is kept");
    }

    #[test]
    fn placeholder_addresses_yield_none_not_garbage() {
        // The shipped example uses placeholder addresses like "0xWETH_ARB" → every
        // representation is unparseable → nothing usable → None (never fabricated).
        let cc = CrossChainConfig {
            enabled: true,
            pairs: vec![["WETH".into(), "USDC".into()]],
            assets: vec![CrossChainAsset {
                symbol: "WETH".into(),
                representations: vec![rep(42161, "0xWETH_ARB", 18), rep(8453, "0xWETH_BASE", 18)],
            }],
            bridges: vec![],
        };
        assert!(build_cross_chain(&cfg_with(cc)).is_none());
    }

    #[test]
    fn disabled_is_none() {
        let cc = CrossChainConfig {
            enabled: false,
            pairs: vec![],
            assets: vec![],
            bridges: vec![],
        };
        assert!(build_cross_chain(&cfg_with(cc)).is_none());
    }
}
