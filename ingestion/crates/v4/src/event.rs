//! Uniswap V4 `PoolManager` event decoding.
//!
//! V4 is a singleton: one `PoolManager` emits every pool's events, keyed by the
//! indexed `poolId` (topic 1). `Swap` carries post-swap `sqrtPriceX96`,
//! `liquidity`, `tick`, and the effective `fee` (crucial for dynamic-fee pools);
//! `ModifyLiquidity` carries a signed `liquidityDelta`.

use crate::error::{Result, V4Error};
use alloy_primitives::{B256, I256, U256};
use alloy_sol_types::{sol, SolEvent};
use l2i_rpc::Log;

sol! {
    /// `PoolManager.Swap` (V4).
    event Swap(
        bytes32 indexed id,
        address indexed sender,
        int128 amount0,
        int128 amount1,
        uint160 sqrtPriceX96,
        uint128 liquidity,
        int24 tick,
        uint24 fee
    );

    /// `PoolManager.ModifyLiquidity` (V4).
    event ModifyLiquidity(
        bytes32 indexed id,
        address indexed sender,
        int24 tickLower,
        int24 tickUpper,
        int256 liquidityDelta,
        bytes32 salt
    );
}

/// Post-swap V4 pool state decoded from a `Swap` log.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct V4SwapState {
    /// The pool this swap belongs to.
    pub pool_id: B256,
    /// √price · 2⁹⁶ post-swap.
    pub sqrt_price_x96: U256,
    /// In-range liquidity post-swap.
    pub liquidity: U256,
    /// Current tick post-swap.
    pub tick: i32,
    /// The effective fee for this swap (the live value for dynamic-fee pools).
    pub fee: u32,
}

/// A V4 `ModifyLiquidity` change.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct V4LiquidityChange {
    /// The pool.
    pub pool_id: B256,
    /// Lower tick (inclusive).
    pub tick_lower: i32,
    /// Upper tick (exclusive).
    pub tick_upper: i32,
    /// Signed liquidity delta (positive = add, negative = remove).
    pub liquidity_delta: I256,
}

fn int24_from_word(word: &[u8]) -> i32 {
    i32::from_be_bytes([word[28], word[29], word[30], word[31]])
}

/// The V4 `Swap` topic0.
pub fn v4_swap_topic() -> B256 {
    Swap::SIGNATURE_HASH
}

/// The V4 `ModifyLiquidity` topic0.
pub fn v4_modify_liquidity_topic() -> B256 {
    ModifyLiquidity::SIGNATURE_HASH
}

/// Decode a V4 `Swap` from its `poolId` (topic 1) and 192-byte data payload.
pub fn decode_v4_swap_parts(pool_id: B256, data: &[u8]) -> Result<V4SwapState> {
    if data.len() != 192 {
        return Err(V4Error::Decode(format!(
            "V4 Swap data must be 192 bytes (6 words), got {}",
            data.len()
        )));
    }
    Ok(V4SwapState {
        pool_id,
        sqrt_price_x96: U256::from_be_slice(&data[64..96]), // word 2
        liquidity: U256::from_be_slice(&data[96..128]),     // word 3
        tick: int24_from_word(&data[128..160]),             // word 4
        // word 5 is `uint24` fee, right-aligned. Read the low 4 bytes rather than
        // `U256::to::<u32>()`, which panics on a malformed word (dirty high bits) —
        // a bad log must be a decode error/skip, never a decode-thread crash.
        fee: u32::from_be_bytes([data[188], data[189], data[190], data[191]]),
    })
}

/// Decode a V4 `Swap` log into [`V4SwapState`], verifying topic0.
pub fn decode_v4_swap(log: &Log) -> Result<V4SwapState> {
    let topics = log.topics();
    match topics.first() {
        Some(t) if *t == Swap::SIGNATURE_HASH => {}
        _ => return Err(V4Error::Decode("log topic0 is not V4 Swap".into())),
    }
    if topics.len() < 2 {
        return Err(V4Error::Decode("V4 Swap missing poolId topic".into()));
    }
    decode_v4_swap_parts(topics[1], &log.inner.data.data)
}

/// Decode a V4 `ModifyLiquidity` log.
pub fn decode_v4_modify_liquidity(log: &Log) -> Result<V4LiquidityChange> {
    let topics = log.topics();
    match topics.first() {
        Some(t) if *t == ModifyLiquidity::SIGNATURE_HASH => {}
        _ => {
            return Err(V4Error::Decode(
                "log topic0 is not V4 ModifyLiquidity".into(),
            ))
        }
    }
    if topics.len() < 2 {
        return Err(V4Error::Decode(
            "V4 ModifyLiquidity missing poolId topic".into(),
        ));
    }
    let data = &log.inner.data.data;
    if data.len() < 96 {
        return Err(V4Error::Decode("V4 ModifyLiquidity data too short".into()));
    }
    Ok(V4LiquidityChange {
        pool_id: topics[1],
        tick_lower: int24_from_word(&data[0..32]),
        tick_upper: int24_from_word(&data[32..64]),
        liquidity_delta: I256::from_be_bytes::<32>(data[64..96].try_into().unwrap()),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn topics_are_canonical() {
        assert_eq!(
            v4_swap_topic(),
            "0x40e9cecb9f5f1f1c5b9c97dec2917b7ee92e57ba5563708daca94dd84ad7112f"
                .parse::<B256>()
                .unwrap()
        );
        assert_eq!(
            v4_modify_liquidity_topic(),
            "0xf208f4912782fd25c7f114ca3723a2d5dd6f3bcc3ac8db5af63baa85f711d5ec"
                .parse::<B256>()
                .unwrap()
        );
    }
}
