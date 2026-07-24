//! The block-stamped pool object — the core payload the engine prices.

use crate::dec::DecU256;
use crate::token::Token;
use alloy_primitives::{Address, B256};
use core::fmt;
use core::str::FromStr;
use serde::{de, Deserialize, Deserializer, Serialize, Serializer};

/// The AMM math family the engine applies. Only the two the engine implements
/// exist here; a Uniswap **V4** pool is emitted with `kind: "v3"` (its identity is
/// a `poolId`, but its math is V3 concentrated liquidity — see
/// `docs/ENGINE_CONTRACT.md §4`).
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum PoolKind {
    /// Constant-product `x·y=k` (Uniswap V2 family / volatile Solidly).
    V2,
    /// Concentrated liquidity (Uniswap V3 family / Slipstream / mapped V4).
    V3,
}

/// A pool's on-the-wire identity. V2/V3 pools are a 20-byte contract address; a
/// Uniswap V4 pool is a 32-byte `poolId`. Both render as `0x`-hex and are told
/// apart on deserialize by their nibble length (40 vs 64).
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum PoolAddress {
    /// A 20-byte EVM contract address (V2/V3 pool).
    Contract(Address),
    /// A 32-byte Uniswap V4 `poolId`.
    PoolId(B256),
}

impl PoolAddress {
    /// The V4 `poolId`, if this is a V4 pool identity.
    pub fn pool_id(&self) -> Option<B256> {
        match self {
            PoolAddress::PoolId(id) => Some(*id),
            PoolAddress::Contract(_) => None,
        }
    }

    /// The contract address, if this is a V2/V3 pool identity.
    pub fn contract(&self) -> Option<Address> {
        match self {
            PoolAddress::Contract(a) => Some(*a),
            PoolAddress::PoolId(_) => None,
        }
    }
}

impl From<Address> for PoolAddress {
    fn from(a: Address) -> Self {
        PoolAddress::Contract(a)
    }
}

impl From<B256> for PoolAddress {
    fn from(id: B256) -> Self {
        PoolAddress::PoolId(id)
    }
}

impl fmt::Display for PoolAddress {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            PoolAddress::Contract(a) => write!(f, "{a}"),
            PoolAddress::PoolId(id) => write!(f, "{id}"),
        }
    }
}

impl Serialize for PoolAddress {
    fn serialize<S: Serializer>(&self, s: S) -> Result<S::Ok, S::Error> {
        match self {
            PoolAddress::Contract(a) => a.serialize(s),
            PoolAddress::PoolId(id) => id.serialize(s),
        }
    }
}

impl<'de> Deserialize<'de> for PoolAddress {
    fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        let s = String::deserialize(d)?;
        let hex = s.strip_prefix("0x").unwrap_or(&s);
        match hex.len() {
            40 => Address::from_str(&s)
                .map(PoolAddress::Contract)
                .map_err(de::Error::custom),
            64 => B256::from_str(&s)
                .map(PoolAddress::PoolId)
                .map_err(de::Error::custom),
            n => Err(de::Error::custom(format!(
                "pool identity must be 20 or 32 bytes (40 or 64 hex nibbles), got {n}"
            ))),
        }
    }
}

/// The block a pool's state is true at. `block_hash` is what makes the value
/// verifiable and reorg-aware (see `docs/ENGINE_CONTRACT.md §5`).
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Blockstamp {
    /// The chain the block belongs to.
    pub chain_id: u64,
    /// Block height.
    pub number: u64,
    /// Canonical block hash — pins the state to a specific block, not just a height.
    pub block_hash: B256,
    /// Block timestamp (unix seconds).
    pub timestamp: u64,
}

/// Constant-product reserves. `reserve0` pairs with `token0` (canonical ordering).
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct V2State {
    /// Reserve of `token0` (post-trade, from `Sync`).
    pub reserve0: DecU256,
    /// Reserve of `token1`.
    pub reserve1: DecU256,
}

/// Concentrated-liquidity active-tick state.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct V3State {
    /// √price · 2⁹⁶ (post-trade, from `Swap`).
    pub sqrt_price_x96: DecU256,
    /// Current tick.
    pub tick: i32,
    /// In-range liquidity.
    pub liquidity: DecU256,
}

/// A block-stamped pool, exactly as the engine consumes it.
///
/// Field order mirrors `docs/reference/INTEGRATION.md §3` so the golden
/// serialization is byte-stable. `v2`/`v3` are mutually exclusive and selected by
/// `kind`; the unused one is omitted.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Pool {
    /// Pool identity (contract address, or V4 `poolId`).
    pub address: PoolAddress,
    /// Which AMM math the engine applies.
    pub kind: PoolKind,
    /// Fee in millionths (0.30 % → 3000, 0.05 % → 500).
    pub fee_pips: u32,
    /// `true` only when reproducible from the canonical chain at `blockstamp`
    /// **and** the latest reconciliation matched (`docs/ENGINE_CONTRACT.md §6`).
    pub verified: bool,
    /// Canonical `token0` (`token0.address < token1.address`).
    pub token0: Token,
    /// Canonical `token1`.
    pub token1: Token,
    /// The block this state is true at.
    pub blockstamp: Blockstamp,
    /// Present iff `kind == V2`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub v2: Option<V2State>,
    /// Present iff `kind == V3`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub v3: Option<V3State>,
}
