// SPDX-License-Identifier: MIT
pragma solidity 0.8.20;

/// @title IBalancerVault
/// @notice Minimal Balancer V2 Vault surface for flash loans.
/// @dev The Vault is deployed at the same address on every supported chain:
///      0xBA12222222228d8Ba445958a75a0704d566BF2C8 (verify per chain).
///      Balancer's real signature uses `IERC20[]`; contract types canonicalise
///      to `address` in selector computation, so `address[]` matches exactly.
interface IBalancerVault {
    /// @notice Performs a flash loan of one or more tokens.
    /// @param recipient The contract receiving the funds (must implement receiveFlashLoan).
    /// @param tokens The tokens to borrow.
    /// @param amounts The amounts to borrow, index-aligned with `tokens`.
    /// @param userData Arbitrary bytes passed back to receiveFlashLoan.
    function flashLoan(
        address recipient,
        address[] memory tokens,
        uint256[] memory amounts,
        bytes memory userData
    ) external;
}

/// @title IBalancerFlashLoanRecipient
/// @notice Callback interface the Balancer Vault invokes after transferring funds.
/// @dev Repayment is by transferring `amount + feeAmount` back to the Vault
///      (NOT by approval, unlike Aave).
interface IBalancerFlashLoanRecipient {
    function receiveFlashLoan(
        address[] memory tokens,
        uint256[] memory amounts,
        uint256[] memory feeAmounts,
        bytes memory userData
    ) external;
}
