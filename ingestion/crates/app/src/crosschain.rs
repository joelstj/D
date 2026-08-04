//! Build the engine's `cross_chain` block from config — the conversion the pipeline
//! was missing (it hardcoded `None`, dark-ing the product's entire reason to exist).
//!
//! Representations with an unparseable address are skipped, so a config full of
//! placeholders yields nothing usable rather than garbage; the result is then
//! pruned by [`filter_cross_chain`] to assets present on ≥2 *enabled* chains with
//! a real matching bridge. We never invent a route.
//!
//! [`build_cross_chain`] returns a [`CrossChainBuild`], not a bare
//! `Option<CrossChain>`: a config with `[cross_chain] enabled = true` that ends up
//! with nothing usable (e.g. every representation address is a placeholder, as in
//! the shipped `config.example.toml`) is a materially different situation from
//! cross-chain being off on purpose, and callers (`pipeline.rs`'s startup log,
//! `main.rs --check-config`'s summary) need to tell the two apart.

use alloy_primitives::Address;
use l2i_aggregator::{filter_cross_chain, CrossChainCounts};
use l2i_config::Config;
use l2i_core::{Asset, Bridge, CrossChain, Representation, Token};
use std::collections::HashSet;
use std::str::FromStr;

/// The result of [`build_cross_chain`]: the usable wiring (if any) plus enough
/// diagnostics to distinguish "disabled"/"not configured" from "enabled but
/// silently inert" — used by both `pipeline.rs`'s startup log and
/// `--check-config`'s summary.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct CrossChainBuild {
    /// The usable, filtered cross-chain wiring — `None` if absent, disabled, or
    /// nothing survived parsing + filtering.
    pub cross_chain: Option<CrossChain>,
    /// `[cross_chain]` was present in config with `enabled = true` (independent
    /// of whether anything usable came out of it).
    pub configured_enabled: bool,
    /// Raw, pre-filter config counts — what the operator wrote in `config.toml`.
    /// Populated whenever `[cross_chain]` is present, even if `enabled = false`.
    pub configured_assets: usize,
    /// Raw configured bridge count (see [`Self::configured_assets`]).
    pub configured_bridges: usize,
    /// Raw configured pair count (see [`Self::configured_assets`]).
    pub configured_pairs: usize,
    /// Post-parse-and-filter usable counts. All `0` unless `configured_enabled`.
    pub usable: CrossChainCounts,
}

impl CrossChainBuild {
    /// The "silently resolves to inert" signal: cross-chain was turned **on** in
    /// config but zero usable assets survived parsing + filtering. Distinguished
    /// from intentionally-off (`!configured_enabled`), which is not a problem and
    /// should stay silent — `pipeline.rs` warns on this, not on that.
    pub fn is_inert_despite_enabled(&self) -> bool {
        self.configured_enabled && self.cross_chain.is_none()
    }
}

/// Convert + filter the config's cross-chain wiring into an engine [`CrossChain`],
/// alongside diagnostics explaining the outcome.
pub fn build_cross_chain(config: &Config) -> CrossChainBuild {
    let Some(cc) = config.cross_chain.as_ref() else {
        return CrossChainBuild::default();
    };
    let configured_assets = cc.assets.len();
    let configured_bridges = cc.bridges.len();
    let configured_pairs = cc.pairs.len();
    if !cc.enabled {
        return CrossChainBuild {
            configured_assets,
            configured_bridges,
            configured_pairs,
            ..Default::default()
        };
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

    let enabled_chain_ids: HashSet<u64> = config.enabled_chains().map(|c| c.chain_id).collect();
    let (filtered, usable) = filter_cross_chain(
        CrossChain {
            assets,
            bridges,
            pairs: cc.pairs.clone(),
        },
        &enabled_chain_ids,
    );

    CrossChainBuild {
        cross_chain: if filtered.assets.is_empty() {
            None
        } else {
            Some(filtered)
        },
        configured_enabled: true,
        configured_assets,
        configured_bridges,
        configured_pairs,
        usable,
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
        // WETH on 2 chains (kept: has a matching bridge), USDC on only 1
        // (pruned). Real addresses; all chains here are enabled in the base
        // example config.
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
        let build = build_cross_chain(&cfg_with(cc));
        let out = build.cross_chain.clone().expect("usable cross-chain");
        // WETH survives (2 chains + a real bridge); USDC pruned (1 chain).
        assert_eq!(out.assets.len(), 1);
        assert_eq!(out.assets[0].symbol, "WETH");
        assert_eq!(out.assets[0].representations.len(), 2);
        assert_eq!(out.bridges.len(), 1, "bridge on a surviving symbol is kept");

        assert!(
            !build.is_inert_despite_enabled(),
            "a usable result is not the inert-despite-enabled case"
        );
        assert_eq!(build.configured_assets, 2);
        assert_eq!(build.usable.assets_out, 1);
    }

    #[test]
    fn placeholder_addresses_yield_none_not_garbage() {
        // The shipped example uses placeholder addresses like "0xWETH_ARB" →
        // every representation is unparseable → nothing usable → None (never
        // fabricated) — and since cross_chain was enabled in config, this is
        // exactly the "silently inert" case `is_inert_despite_enabled` exists to
        // flag.
        let cc = CrossChainConfig {
            enabled: true,
            pairs: vec![["WETH".into(), "USDC".into()]],
            assets: vec![CrossChainAsset {
                symbol: "WETH".into(),
                representations: vec![rep(42161, "0xWETH_ARB", 18), rep(8453, "0xWETH_BASE", 18)],
            }],
            bridges: vec![],
        };
        let build = build_cross_chain(&cfg_with(cc));
        assert!(build.cross_chain.is_none());
        assert!(
            build.is_inert_despite_enabled(),
            "enabled in config but nothing usable survived parsing+filtering"
        );
        assert_eq!(build.configured_assets, 1);
        assert_eq!(build.usable.assets_out, 0);
    }

    #[test]
    fn asset_with_representations_but_no_bridge_is_dropped() {
        // WETH parses fine on 2 chains but no [[cross_chain.bridges]] entry
        // references it: representation count alone is not enough, the docs'
        // "a real bridge between them" must also hold, end-to-end through
        // build_cross_chain.
        let cc = CrossChainConfig {
            enabled: true,
            pairs: vec![],
            assets: vec![CrossChainAsset {
                symbol: "WETH".into(),
                representations: vec![
                    rep(42161, "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1", 18),
                    rep(8453, "0x4200000000000000000000000000000000000006", 18),
                ],
            }],
            bridges: vec![],
        };
        let build = build_cross_chain(&cfg_with(cc));
        assert!(build.cross_chain.is_none());
        assert!(build.is_inert_despite_enabled());
    }

    #[test]
    fn drops_representation_on_a_disabled_chain() {
        // WETH has 3 real-address representations (arbitrum, base, optimism)
        // and a bridge between arbitrum and optimism. Disabling `base` in
        // [[chains]] must drop its representation even though the address
        // itself parses fine — leaving 2 (arbitrum, optimism), which still
        // clears the >=2 threshold and keeps its matching bridge.
        let cc = CrossChainConfig {
            enabled: true,
            pairs: vec![],
            assets: vec![CrossChainAsset {
                symbol: "WETH".into(),
                representations: vec![
                    rep(42161, "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1", 18),
                    rep(8453, "0x4200000000000000000000000000000000000006", 18),
                    rep(10, "0x4200000000000000000000000000000000000006", 18),
                ],
            }],
            bridges: vec![CrossChainBridge {
                symbol: "WETH".into(),
                from_chain: 42161,
                to_chain: 10,
                fee_bps: 10.0,
                fixed_fee: 0,
                settle_seconds: 600,
            }],
        };
        let mut cfg = cfg_with(cc);
        cfg.chains
            .iter_mut()
            .find(|c| c.chain_id == 8453)
            .unwrap()
            .enabled = false;

        let build = build_cross_chain(&cfg);
        let out = build
            .cross_chain
            .expect("arbitrum+optimism reps + bridge still usable");
        assert_eq!(out.assets[0].representations.len(), 2);
        assert!(
            out.assets[0]
                .representations
                .iter()
                .all(|r| r.token.chain_id != 8453),
            "the disabled chain's representation must be dropped"
        );
    }

    #[test]
    fn disabled_is_none_and_not_inert() {
        let cc = CrossChainConfig {
            enabled: false,
            pairs: vec![],
            assets: vec![],
            bridges: vec![],
        };
        let build = build_cross_chain(&cfg_with(cc));
        assert!(build.cross_chain.is_none());
        assert!(
            !build.is_inert_despite_enabled(),
            "intentionally disabled must not be reported as the silently-inert case"
        );
        assert!(!build.configured_enabled);
    }

    #[test]
    fn absent_cross_chain_block_is_none_and_not_inert() {
        let mut cfg = Config::load(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../config/config.example.toml"
        ))
        .unwrap();
        cfg.cross_chain = None;

        let build = build_cross_chain(&cfg);
        assert!(build.cross_chain.is_none());
        assert!(!build.configured_enabled);
        assert!(!build.is_inert_despite_enabled());
        assert_eq!(build.configured_assets, 0);
    }
}
