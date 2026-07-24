//! ERC-20 token identity as the engine expects it.

use alloy_primitives::Address;
use serde::{Deserialize, Serialize};

/// A token as it appears inside a pool object or an opportunity leg.
///
/// `symbol` is present for pool/opportunity tokens (we read it on-chain and cache
/// it) but omitted for cross-chain asset *representations*, whose contract shape
/// carries only `{chain_id, address, decimals}` (see
/// `docs/reference/INTEGRATION.md §3`). It is therefore `Option`, skipped when
/// `None` so both shapes serialize exactly.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Token {
    /// The chain this token lives on.
    pub chain_id: u64,
    /// The ERC-20 contract address.
    pub address: Address,
    /// Decimals read from the ERC-20 (`0..=36`, validated at ingest).
    pub decimals: u8,
    /// The ERC-20 symbol, when this token appears in a pool/opportunity context.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub symbol: Option<String>,
}

impl Token {
    /// A token with a symbol (pool / opportunity context).
    pub fn with_symbol(
        chain_id: u64,
        address: Address,
        decimals: u8,
        symbol: impl Into<String>,
    ) -> Self {
        Self {
            chain_id,
            address,
            decimals,
            symbol: Some(symbol.into()),
        }
    }

    /// A token without a symbol (cross-chain representation context).
    pub fn bare(chain_id: u64, address: Address, decimals: u8) -> Self {
        Self {
            chain_id,
            address,
            decimals,
            symbol: None,
        }
    }
}
