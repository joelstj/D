// SPDX-License-Identifier: MIT
pragma solidity 0.8.20;

/// @title IBridgeAdapter
/// @notice Generic adapter over a cross-chain messaging/liquidity bridge
///         (Across, Stargate/LayerZero, CCTP, Hop, native L2 bridges, ...).
/// @dev Kept intentionally minimal so any bridge can be wrapped behind it. The
///      adapter is responsible for pulling `token` from the caller (or being
///      pre-approved) and initiating the transfer to `dstChainId`.
interface IBridgeAdapter {
    /// @notice Sends `amount` of `token` to `recipient` on `dstChainId`.
    /// @param token The token to bridge.
    /// @param amount The amount to bridge.
    /// @param dstChainId The destination chain id.
    /// @param recipient The recipient address on the destination chain.
    /// @param options Bridge-specific encoded options (fees, slippage, relayer data...).
    function bridge(address token, uint256 amount, uint256 dstChainId, address recipient, bytes calldata options)
        external
        payable;
}
