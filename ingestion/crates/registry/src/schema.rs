//! The per-chain pool registry schema and TOML loader.
//!
//! Mirrors `config/pools/README.md`. Only pool types the engine can price may be
//! listed — constant-product (`v2`), Uniswap-V3-style concentrated liquidity
//! (`v3`), and Uniswap **V4** pools (`v4`, emitted to the engine as `v3`). Every
//! entry is proven on-chain by the validation gate (`gate.rs`) before it enters
//! the live set.

use alloy_primitives::{Address, B256};
use serde::{Deserialize, Serialize};

/// A parsed per-chain registry file (`config/pools/<chain>.toml`).
#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct PoolRegistry {
    /// The pools this chain's ingestor tracks.
    #[serde(default, rename = "pool")]
    pub pools: Vec<PoolEntry>,
}

/// One registry entry. Tagged by `kind` (`v2` | `v3` | `v4`); the fields differ
/// per kind exactly as `config/pools/README.md` documents.
#[derive(Clone, Debug, PartialEq, Eq, Deserialize, Serialize)]
#[serde(tag = "kind", rename_all = "lowercase")]
pub enum PoolEntry {
    /// Constant-product pair (Uniswap V2 family / volatile Solidly).
    V2(V2V3Entry),
    /// Concentrated liquidity (Uniswap V3 family / Slipstream).
    V3(V2V3Entry),
    /// Uniswap V4 singleton pool (identity is the `poolId`).
    V4(V4Entry),
}

/// A V2 or V3 entry (same shape: a pool contract with two ordered tokens).
#[derive(Clone, Debug, PartialEq, Eq, Deserialize, Serialize)]
pub struct V2V3Entry {
    /// Informational DEX label (the factory is what's verified on-chain).
    #[serde(default)]
    pub dex: String,
    /// The pool/pair contract address.
    pub address: Address,
    /// Declared fee in millionths (validated on-chain for V3; a protocol constant
    /// for V2, which exposes no per-pair `fee()`).
    pub fee_pips: u32,
    /// Declared `token0` (must equal on-chain `token0()`, canonical byte order).
    pub token0: Address,
    /// Declared `token1` (must equal on-chain `token1()`).
    pub token1: Address,
    /// Optional expected factory; when set, the gate asserts on-chain `factory()`
    /// equals it (guards against look-alike/malicious pools).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub factory: Option<Address>,
}

/// A V4 entry. Identity is the `poolId`; the full `PoolKey` is retained so the
/// downstream executor can reconstruct the route.
#[derive(Clone, Debug, PartialEq, Eq, Deserialize, Serialize)]
pub struct V4Entry {
    /// Informational DEX label.
    #[serde(default)]
    pub dex: String,
    /// The 32-byte `poolId` (becomes the engine pool `address`).
    pub id: B256,
    /// `PoolKey.currency0` (canonical byte order).
    pub currency0: Address,
    /// `PoolKey.currency1`.
    pub currency1: Address,
    /// `PoolKey.fee` (millionths), or `0x800000` for dynamic-fee.
    pub fee: u32,
    /// `PoolKey.tickSpacing`.
    pub tick_spacing: i32,
    /// `PoolKey.hooks` (must be `0x0` or on the chain's `safe_hooks` allow-list).
    pub hooks: Address,
}

/// The Uniswap V4 dynamic-fee sentinel (`PoolKey.fee == 0x800000`).
pub const DYNAMIC_FEE_FLAG: u32 = 0x800000;

impl PoolEntry {
    /// The pool identity as it will appear to the engine (contract address for
    /// V2/V3, `poolId` for V4).
    pub fn identity(&self) -> l2i_core::PoolAddress {
        match self {
            PoolEntry::V2(e) | PoolEntry::V3(e) => l2i_core::PoolAddress::Contract(e.address),
            PoolEntry::V4(e) => l2i_core::PoolAddress::PoolId(e.id),
        }
    }

    /// `(token0, token1)` as declared.
    pub fn tokens(&self) -> (Address, Address) {
        match self {
            PoolEntry::V2(e) | PoolEntry::V3(e) => (e.token0, e.token1),
            PoolEntry::V4(e) => (e.currency0, e.currency1),
        }
    }

    /// The declared fee in millionths.
    pub fn fee_pips(&self) -> u32 {
        match self {
            PoolEntry::V2(e) | PoolEntry::V3(e) => e.fee_pips,
            PoolEntry::V4(e) => e.fee,
        }
    }

    /// The `kind` label as the engine expects it (`v2`/`v3`; V4 maps to `v3`).
    pub fn engine_kind(&self) -> l2i_core::PoolKind {
        match self {
            PoolEntry::V2(_) => l2i_core::PoolKind::V2,
            PoolEntry::V3(_) | PoolEntry::V4(_) => l2i_core::PoolKind::V3,
        }
    }
}

/// Parse a registry from TOML text.
pub fn parse_registry(toml_src: &str) -> Result<PoolRegistry, toml::de::Error> {
    toml::from_str(toml_src)
}

#[cfg(test)]
mod tests {
    use super::*;
    use alloy_primitives::address;

    #[test]
    fn parses_all_three_kinds() {
        let src = r#"
            [[pool]]
            dex = "uniswap_v2"
            kind = "v2"
            address = "0x905dfCD5649217c42684f23958568e533C711Aa3"
            fee_pips = 3000
            token0 = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"
            token1 = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"

            [[pool]]
            dex = "uniswap_v3"
            kind = "v3"
            address = "0xC6962004f452bE9203591991D15f6b388e09E8D0"
            fee_pips = 500
            token0 = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"
            token1 = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
            factory = "0x1F98431c8aD98523631AE4a59f267346ea31F984"

            [[pool]]
            dex = "uniswap_v4"
            kind = "v4"
            id = "0x21c67e77068de97969ba93d4aab21826d33ca12bb9f565d8496e8fda8a82ca27"
            currency0 = "0x0000000000000000000000000000000000000000"
            currency1 = "0x078D782b760474a361dDA0AF3839290b0EF57AD6"
            fee = 500
            tick_spacing = 10
            hooks = "0x0000000000000000000000000000000000000000"
        "#;
        let reg = parse_registry(src).unwrap();
        assert_eq!(reg.pools.len(), 3);
        assert!(matches!(reg.pools[0], PoolEntry::V2(_)));
        assert!(matches!(reg.pools[1], PoolEntry::V3(_)));
        assert!(matches!(reg.pools[2], PoolEntry::V4(_)));
        assert_eq!(reg.pools[1].fee_pips(), 500);
        assert_eq!(reg.pools[2].engine_kind(), l2i_core::PoolKind::V3);
        assert_eq!(
            reg.pools[0].tokens().0,
            address!("82aF49447D8a07e3bd95BD0d56f35241523fBab1")
        );
    }

    #[test]
    fn empty_registry_is_ok() {
        let reg = parse_registry("").unwrap();
        assert!(reg.pools.is_empty());
    }

    #[test]
    fn unknown_kind_rejected() {
        let src = r#"
            [[pool]]
            kind = "curve"
            address = "0x0000000000000000000000000000000000000001"
        "#;
        assert!(parse_registry(src).is_err());
    }
}
