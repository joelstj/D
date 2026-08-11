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
    /// Uniswap V3 Factory `getPool(tokenA, tokenB, fee)` — the zero address means
    /// no pool exists for that pair/fee. Used by pool discovery, never by the
    /// live path (the gate validates *declared* pools; it never looks any up).
    function getPool(address tokenA, address tokenB, uint24 fee) external view returns (address pool);
    /// Uniswap V3 Factory `feeAmountTickSpacing(fee)` — a standard, non-zero fee
    /// tier (500/3000/10000) always maps to a non-zero tick spacing on a genuine
    /// factory. Discovery fingerprints a candidate factory address with this
    /// before trusting anything it reports; a look-alike/wrong-address contract
    /// answers something else (or reverts) and is rejected, never silently
    /// treated as real.
    function feeAmountTickSpacing(uint24 fee) external view returns (int24 tickSpacing);

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
/// Calldata for `getPool(tokenA, tokenB, fee)`.
pub fn get_pool_calldata(token_a: Address, token_b: Address, fee: u32) -> Bytes {
    getPoolCall {
        tokenA: token_a,
        tokenB: token_b,
        fee: U24::from(fee),
    }
    .abi_encode()
    .into()
}
/// Calldata for `feeAmountTickSpacing(fee)`.
pub fn fee_amount_tick_spacing_calldata(fee: u32) -> Bytes {
    feeAmountTickSpacingCall {
        fee: U24::from(fee),
    }
    .abi_encode()
    .into()
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

/// Decode a `getPool` return (an `address`; the zero address means no pool).
pub fn decode_pool_address(ret: &[u8]) -> Result<Address, GateError> {
    getPoolCall::abi_decode_returns(ret).map_err(|e| GateError::Decode(format!("getPool: {e}")))
}

/// Decode a `feeAmountTickSpacing` return into a plain `i32`.
pub fn decode_tick_spacing(ret: &[u8]) -> Result<i32, GateError> {
    let v = feeAmountTickSpacingCall::abi_decode_returns(ret)
        .map_err(|e| GateError::Decode(format!("feeAmountTickSpacing: {e}")))?;
    i32::try_from(v).map_err(|e| GateError::Decode(format!("feeAmountTickSpacing: {e}")))
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
        // Uniswap V3 Factory selectors used by pool discovery — pinned here so a
        // hand-rolled ABI encoder elsewhere in the repo (e.g. a Python discovery
        // script that can't compute Keccak256 itself without a new dependency)
        // has one real, tested source of truth to hardcode against instead of
        // trusting anyone's memory of the spec.
        assert_eq!(getPoolCall::SELECTOR, [0x16, 0x98, 0xee, 0x82]);
        assert_eq!(feeAmountTickSpacingCall::SELECTOR, [0x22, 0xaf, 0xcc, 0xcb]);
    }

    #[test]
    fn decodes_tick_spacing_and_pool_address() {
        // int24 tickSpacing = 10, right-aligned in a 32-byte word.
        let mut ret = [0u8; 32];
        ret[31] = 10;
        assert_eq!(decode_tick_spacing(&ret).unwrap(), 10);

        // address pool, left-zero-padded in a 32-byte word.
        let mut ret = [0u8; 32];
        ret[12..].copy_from_slice(&[0x11; 20]);
        assert_eq!(
            decode_pool_address(&ret).unwrap(),
            Address::from([0x11; 20])
        );
    }

    #[test]
    fn symbol_bytes32_fallback() {
        // "MKR" right-padded to 32 bytes.
        let mut b = [0u8; 32];
        b[..3].copy_from_slice(b"MKR");
        assert_eq!(decode_symbol(&b).unwrap(), "MKR");
    }
}
