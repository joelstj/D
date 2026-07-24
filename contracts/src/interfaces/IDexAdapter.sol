// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

/// @title IDexAdapter
/// @notice Uniform swap interface every DEX family sits behind (docs/specs/03-dex-adapters.md).
///         `quote()` MUST match the realized `swap()` output within rounding on a fork.
interface IDexAdapter {
    /// @notice Swap exactly `amountIn` of `tokenIn` for `tokenOut` on `pool`, requiring >= `minOut`.
    /// @param data venue-specific params (fee tier, poolKey, bin ids, stable flag, hook) from the route.
    /// @return amountOut actual output, sent to `to`.
    function swap(
        address pool,
        address tokenIn,
        address tokenOut,
        uint256 amountIn,
        uint256 minOut,
        address to,
        bytes calldata data
    ) external returns (uint256 amountOut);

    /// @notice Pure/view quote used off-chain and by on-chain sizing guards.
    function quote(address pool, address tokenIn, address tokenOut, uint256 amountIn, bytes calldata data)
        external
        view
        returns (uint256 amountOut);
}
