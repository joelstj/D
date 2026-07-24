//! Event decoding — the latency win.
//!
//! State is read *out of the log the node already pushed*: a V2 `Sync` carries the
//! post-trade reserves directly, so the hot path needs no `getReserves` round-trip.
//! We decode straight from the log's data.

use crate::error::{IngestError, Result};
use alloy_primitives::{B256, U256};
use alloy_sol_types::{sol, SolEvent};
use l2i_rpc::Log;

sol! {
    /// UniswapV2Pair `Sync` — emitted after every mint/burn/swap with the new
    /// reserves. No indexed fields, so both values live in `data`.
    event Sync(uint112 reserve0, uint112 reserve1);

    /// UniswapV3Pool `Swap` — carries post-swap `sqrtPriceX96`, `liquidity`, `tick`
    /// (all non-indexed → in `data`; `sender`/`recipient` are indexed).
    event Swap(
        address indexed sender,
        address indexed recipient,
        int256 amount0,
        int256 amount1,
        uint160 sqrtPriceX96,
        uint128 liquidity,
        int24 tick
    );

    /// UniswapV3Pool `Mint` — `tickLower`/`tickUpper` are indexed; `amount` is in data.
    event Mint(
        address sender,
        address indexed owner,
        int24 indexed tickLower,
        int24 indexed tickUpper,
        uint128 amount,
        uint256 amount0,
        uint256 amount1
    );

    /// UniswapV3Pool `Burn` — `tickLower`/`tickUpper` indexed; `amount` in data.
    event Burn(
        address indexed owner,
        int24 indexed tickLower,
        int24 indexed tickUpper,
        uint128 amount,
        uint256 amount0,
        uint256 amount1
    );
}

/// A `Mint`/`Burn` in-range liquidity change.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct LiquidityChange {
    /// Lower tick of the modified range (inclusive).
    pub tick_lower: i32,
    /// Upper tick of the modified range (exclusive).
    pub tick_upper: i32,
    /// Liquidity amount added (`Mint`) or removed (`Burn`).
    pub amount: U256,
    /// `true` = `Mint` (add), `false` = `Burn` (remove).
    pub add: bool,
}

/// Read the low 4 bytes of a 32-byte word as an `i32` — exact for an `int24`
/// sign-extended into the word (the sign bit propagates into bit 31).
fn int24_from_word(word: &[u8]) -> i32 {
    i32::from_be_bytes([word[28], word[29], word[30], word[31]])
}

/// The V3 `Swap` topic0.
pub fn v3_swap_topic() -> B256 {
    Swap::SIGNATURE_HASH
}

/// Decode a V3 `Swap`'s `(sqrtPriceX96, liquidity, tick)` from its 160-byte data.
pub fn decode_v3_swap_data(data: &[u8]) -> Result<(U256, U256, i32)> {
    if data.len() != 160 {
        return Err(IngestError::Decode(format!(
            "V3 Swap data must be 160 bytes (5 words), got {}",
            data.len()
        )));
    }
    let sqrt_price_x96 = U256::from_be_slice(&data[64..96]); // word 2
    let liquidity = U256::from_be_slice(&data[96..128]); // word 3 (uint128)
    let tick = int24_from_word(&data[128..160]); // word 4 (int24)
    Ok((sqrt_price_x96, liquidity, tick))
}

/// Decode a V3 `Swap` from a full log, verifying its topic0.
pub fn decode_v3_swap(log: &Log) -> Result<(U256, U256, i32)> {
    match log.topics().first() {
        Some(t) if *t == Swap::SIGNATURE_HASH => {}
        _ => return Err(IngestError::Decode("log topic0 is not V3 Swap".into())),
    }
    decode_v3_swap_data(&log.inner.data.data)
}

/// Decode a V3 `Mint`/`Burn` into a [`LiquidityChange`]. `tickLower`/`tickUpper`
/// come from the indexed topics; `amount` from data.
pub fn decode_v3_liquidity_change(log: &Log) -> Result<LiquidityChange> {
    let topics = log.topics();
    let (add, amount_word) = match topics.first() {
        Some(t) if *t == Mint::SIGNATURE_HASH => (true, 1usize), // data: [sender, amount, ...]
        Some(t) if *t == Burn::SIGNATURE_HASH => (false, 0usize), // data: [amount, ...]
        _ => return Err(IngestError::Decode("log topic0 is not V3 Mint/Burn".into())),
    };
    if topics.len() < 4 {
        return Err(IngestError::Decode("V3 Mint/Burn needs 4 topics".into()));
    }
    let tick_lower = int24_from_word(topics[2].as_slice());
    let tick_upper = int24_from_word(topics[3].as_slice());
    let data = &log.inner.data.data;
    let start = amount_word * 32;
    if data.len() < start + 32 {
        return Err(IngestError::Decode("V3 Mint/Burn data too short".into()));
    }
    let amount = U256::from_be_slice(&data[start..start + 32]);
    Ok(LiquidityChange {
        tick_lower,
        tick_upper,
        amount,
        add,
    })
}

/// The `Sync` event's topic0 (its signature hash).
pub fn sync_topic() -> B256 {
    Sync::SIGNATURE_HASH
}

/// Decode a `Sync` event's reserves straight from its 64-byte data payload.
pub fn decode_sync_reserves(data: &[u8]) -> Result<(U256, U256)> {
    if data.len() != 64 {
        return Err(IngestError::Decode(format!(
            "Sync data must be 64 bytes (two uint112 words), got {}",
            data.len()
        )));
    }
    // Each uint112 is right-aligned in a 32-byte word; the value fits in U256.
    let reserve0 = U256::from_be_slice(&data[0..32]);
    let reserve1 = U256::from_be_slice(&data[32..64]);
    Ok((reserve0, reserve1))
}

/// Decode a `Sync` from a full log, verifying its topic0 first.
pub fn decode_sync(log: &Log) -> Result<(U256, U256)> {
    match log.topics().first() {
        Some(t) if *t == Sync::SIGNATURE_HASH => {}
        _ => return Err(IngestError::Decode("log topic0 is not Sync".into())),
    }
    decode_sync_reserves(&log.inner.data.data)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sync_topic_is_canonical() {
        // keccak256("Sync(uint112,uint112)").
        assert_eq!(
            sync_topic(),
            "0x1c411e9a96e071241c2f21f7726b17ae89e3cab4c78be50e062b03a9fffbbad1"
                .parse::<B256>()
                .unwrap()
        );
    }

    #[test]
    fn rejects_wrong_length() {
        assert!(decode_sync_reserves(&[0u8; 32]).is_err());
    }

    #[test]
    fn decodes_two_words() {
        let mut data = [0u8; 64];
        data[31] = 5; // reserve0 = 5
        data[63] = 9; // reserve1 = 9
        let (r0, r1) = decode_sync_reserves(&data).unwrap();
        assert_eq!(r0, U256::from(5u8));
        assert_eq!(r1, U256::from(9u8));
    }
}
