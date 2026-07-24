// SPDX-License-Identifier: MIT
pragma solidity 0.8.20;

/// @title IUniswapV2Router
/// @notice Minimal UniswapV2-compatible router surface (Uniswap, SushiSwap,
///         Camelot V2, BaseSwap, QuickSwap, Velodrome-compat, etc.).
interface IUniswapV2Router {
    /// @notice Swaps an exact amount of input tokens along `path` for as many output tokens as possible.
    function swapExactTokensForTokens(
        uint256 amountIn,
        uint256 amountOutMin,
        address[] calldata path,
        address to,
        uint256 deadline
    ) external returns (uint256[] memory amounts);
}

/// @title IUniswapV2Pair
/// @notice Minimal pair surface used to read reserves for optimal loan sizing.
interface IUniswapV2Pair {
    /// @notice Returns the pair reserves and the last update timestamp.
    function getReserves() external view returns (uint112 reserve0, uint112 reserve1, uint32 blockTimestampLast);

    /// @notice The lower-sorted token of the pair.
    function token0() external view returns (address);

    /// @notice The higher-sorted token of the pair.
    function token1() external view returns (address);
}
