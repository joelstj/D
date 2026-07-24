// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

/// @title IFlashProvider
/// @notice Uniform flash-loan interface over Aave V3, Balancer V2, Uniswap V3/V4, and DEX-native
///         flash-swaps (docs/specs/02-flash-loans.md). The concrete adapter normalizes each
///         provider's native callback into the executor's validated FlashRouter.
interface IFlashProvider {
    /// @notice Borrow `amount` of `token`, invoking the executor's callback with repayment owed.
    /// @param data opaque payload the executor uses to resume the route inside the callback.
    function flashBorrow(address token, uint256 amount, bytes calldata data) external;

    /// @notice Cost of borrowing `amount` of `token` from this provider, in `token` units.
    function flashFee(address token, uint256 amount) external view returns (uint256);
}
