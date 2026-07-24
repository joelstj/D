// SPDX-License-Identifier: MIT
pragma solidity 0.8.20;

/// @title ISwapRouter02
/// @notice Minimal Uniswap V3 SwapRouter02 surface.
/// @dev SwapRouter02 (the version deployed on Optimism, Arbitrum, Base,
///      Polygon and Unichain) dropped the `deadline` field from the params
///      struct that the original SwapRouter used. For the legacy SwapRouter or
///      any non-standard fork, use DexType.GENERIC instead.
interface ISwapRouter02 {
    struct ExactInputSingleParams {
        address tokenIn;
        address tokenOut;
        uint24 fee;
        address recipient;
        uint256 amountIn;
        uint256 amountOutMinimum;
        uint160 sqrtPriceLimitX96;
    }

    /// @notice Swaps `amountIn` of one token for as much as possible of another single-hop pool.
    function exactInputSingle(ExactInputSingleParams calldata params)
        external
        payable
        returns (uint256 amountOut);

    struct ExactInputParams {
        bytes path;
        address recipient;
        uint256 amountIn;
        uint256 amountOutMinimum;
    }

    /// @notice Swaps `amountIn` of the first token in `path` for as much as possible of the last token.
    function exactInput(ExactInputParams calldata params)
        external
        payable
        returns (uint256 amountOut);
}
