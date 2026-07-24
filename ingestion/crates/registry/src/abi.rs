//! Minimal ABI bindings for the reads the validation gate performs.
//!
//! Pool identity/fee (`token0`/`token1`/`fee`/`factory`), ERC-20 metadata
//! (`decimals`/`symbol`), and the V4 `StateView` reads. Only what the gate and the
//! ingestors need — not a full DEX ABI.

use crate::error::GateError;
use alloy_primitives::aliases::{I24, U24};
use alloy_primitives::{keccak256, Address, Bytes, B256};
use alloy_sol_types::{sol, SolCall, SolValue};

sol! {
    /// UniswapV2Pair / V3Pool `token0()`.
    function token0() external view returns (address);
    /// `token1()`.
    function token1() external view returns (address);
    /// UniswapV3Pool `fee()` (uint24 millionths).
    function fee() external view returns (uint24);
    /// Pool `factory()`.
    function factory() external view returns (address);
    /// ERC-20 `decimals()`.
    function decimals() external view returns (uint8);
    /// ERC-20 `symbol()` (string form; some legacy tokens use bytes32 — handled
    /// by [`decode_symbol`]).
    function symbol() external view returns (string);
    /// UniswapV2Pair `getReserves()`.
    function getReserves() external view returns (uint112 reserve0, uint112 reserve1, uint32 blockTimestampLast);

    /// V4 `StateView.getSlot0(poolId)`.
    function getSlot0(bytes32 poolId) external view returns (uint160 sqrtPriceX96, int24 tick, uint24 protocolFee, uint24 lpFee);
    /// V4 `StateView.getLiquidity(poolId)`.
    function getLiquidity(bytes32 poolId) external view returns (uint128 liquidity);

    /// Uniswap V4 `PoolKey`. `poolId = keccak256(abi.encode(PoolKey))`.
    struct PoolKey {
        address currency0;
        address currency1;
        uint24 fee;
        int24 tickSpacing;
        address hooks;
    }
}

/// Compute the V4 `poolId` for a `PoolKey`: `keccak256(abi.encode(PoolKey))`.
pub fn compute_pool_id(
    currency0: Address,
    currency1: Address,
    fee: u32,
    tick_spacing: i32,
    hooks: Address,
) -> B256 {
    let key = PoolKey {
        currency0,
        currency1,
        fee: U24::from(fee),
        tickSpacing: I24::try_from(tick_spacing).unwrap_or(I24::ZERO),
        hooks,
    };
    keccak256(key.abi_encode())
}

/// Calldata for `token0()`.
pub fn token0_calldata() -> Bytes {
    token0Call {}.abi_encode().into()
}
/// Calldata for `token1()`.
pub fn token1_calldata() -> Bytes {
    token1Call {}.abi_encode().into()
}
/// Calldata for `fee()`.
pub fn fee_calldata() -> Bytes {
    feeCall {}.abi_encode().into()
}
/// Calldata for `factory()`.
pub fn factory_calldata() -> Bytes {
    factoryCall {}.abi_encode().into()
}
/// Calldata for `decimals()`.
pub fn decimals_calldata() -> Bytes {
    decimalsCall {}.abi_encode().into()
}
/// Calldata for `symbol()`.
pub fn symbol_calldata() -> Bytes {
    symbolCall {}.abi_encode().into()
}

/// Decode an `address` return (`token0`/`token1`/`factory`).
pub fn decode_address(ret: &[u8]) -> Result<Address, GateError> {
    token0Call::abi_decode_returns(ret).map_err(|e| GateError::Decode(format!("address: {e}")))
}

/// Decode a `uint24` fee return into `u32` millionths.
pub fn decode_fee(ret: &[u8]) -> Result<u32, GateError> {
    let v = feeCall::abi_decode_returns(ret).map_err(|e| GateError::Decode(format!("fee: {e}")))?;
    Ok(v.to::<u32>())
}

/// Decode a `uint8` decimals return.
pub fn decode_decimals(ret: &[u8]) -> Result<u8, GateError> {
    decimalsCall::abi_decode_returns(ret).map_err(|e| GateError::Decode(format!("decimals: {e}")))
}

/// Decode an ERC-20 `symbol()`, tolerating both the `string` and legacy `bytes32`
/// encodings (e.g. MKR/SAI return a fixed 32-byte symbol).
pub fn decode_symbol(ret: &[u8]) -> Result<String, GateError> {
    if let Ok(s) = symbolCall::abi_decode_returns(ret) {
        return Ok(s);
    }
    // bytes32 fallback: a raw, right-zero-padded 32-byte symbol.
    if ret.len() == 32 {
        let end = ret.iter().position(|&b| b == 0).unwrap_or(32);
        if let Ok(s) = std::str::from_utf8(&ret[..end]) {
            if !s.is_empty() {
                return Ok(s.to_string());
            }
        }
    }
    Err(GateError::Decode(
        "symbol: neither string nor bytes32".into(),
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn selectors_are_stable() {
        // Canonical ERC-20 / Uniswap selectors.
        assert_eq!(token0Call::SELECTOR, [0x0d, 0xfe, 0x16, 0x81]);
        assert_eq!(token1Call::SELECTOR, [0xd2, 0x12, 0x20, 0xa7]);
        assert_eq!(feeCall::SELECTOR, [0xdd, 0xca, 0x3f, 0x43]);
        assert_eq!(decimalsCall::SELECTOR, [0x31, 0x3c, 0xe5, 0x67]);
        assert_eq!(symbolCall::SELECTOR, [0x95, 0xd8, 0x9b, 0x41]);
        assert_eq!(factoryCall::SELECTOR, [0xc4, 0x5a, 0x01, 0x55]);
    }

    #[test]
    fn symbol_bytes32_fallback() {
        // "MKR" right-padded to 32 bytes.
        let mut b = [0u8; 32];
        b[..3].copy_from_slice(b"MKR");
        assert_eq!(decode_symbol(&b).unwrap(), "MKR");
    }
}
