// SPDX-License-Identifier: MIT
pragma solidity 0.8.20;

/// @title ArbTypes
/// @notice Shared enums and structs for the flash-loan arbitrage engine.
/// @dev Defined at file level so both the executor library and the main
///      contract can share a single canonical ABI definition.

/// @notice Supported flash-loan liquidity providers.
/// @dev AAVE_V3 charges a small premium (see pool.FLASHLOAN_PREMIUM_TOTAL, ~0.05%).
///      BALANCER_V2 is typically 0-fee on L2s (verify per chain).
enum FlashProvider {
    AAVE_V3,
    BALANCER_V2
}

/// @notice Supported DEX call shapes for a single hop.
/// @dev GENERIC is an escape hatch: the off-chain caller supplies raw calldata
///      and (optionally) a byte offset at which the runtime input amount is
///      patched in, so any exotic venue can be integrated without a contract
///      upgrade.
enum DexType {
    UNISWAP_V2, // swapExactTokensForTokens(uint,uint,address[],address,uint)
    UNISWAP_V3_SINGLE, // exactInputSingle(...)  (SwapRouter02 struct, no deadline)
    UNISWAP_V3_MULTI, // exactInput(bytes path, ...)
    CURVE, // exchange(int128,int128,uint256,uint256)
    GENERIC // arbitrary low-level call, with optional amountIn patch
}

/// @notice A single hop in an arbitrage route.
/// @dev The engine always swaps the *entire* running balance forward, so the
///      route auto-adapts to any loan size (dynamic sizing) without the caller
///      pre-computing intermediate amounts. Only the fields relevant to
///      `dexType` need to be populated; the rest can be left zero to save
///      calldata gas.
struct SwapStep {
    DexType dexType; // which call shape to use
    address router; // router/pool to approve and call
    address tokenIn; // token spent on this hop
    address tokenOut; // token received on this hop (used for accounting)
    uint24 poolFee; // Uniswap V3 fee tier (e.g. 100/500/3000/10000)
    int128 curveI; // Curve coin index of tokenIn
    int128 curveJ; // Curve coin index of tokenOut
    uint256 minOut; // per-hop slippage floor for tokenOut (0 = skip check)
    bytes data; // V3_MULTI: encoded path | GENERIC: raw calldata
    uint256 amountInOffset; // GENERIC only: calldata offset to patch amountIn (0 = none)
}

/// @notice Full parameter set for an atomic arbitrage execution.
struct ArbParams {
    FlashProvider provider; // where to borrow from
    address asset; // token to borrow and repay (route must start and end here)
    uint256 amount; // loan size (choose off-chain, or via quoteOptimalTwoHopV2)
    uint256 minProfit; // revert unless net profit (in `asset`) >= this
    address profitReceiver; // where net profit is sent (0 => tx executor)
    uint256 deadline; // unix seconds; revert if block.timestamp exceeds it
    SwapStep[] steps; // ordered hops: steps[0].tokenIn == asset == steps[n-1].tokenOut
}
