// SPDX-License-Identifier: MIT
pragma solidity 0.8.20;

/// @title IAaveV3Pool
/// @notice Minimal Aave V3 Pool surface required for single-asset flash loans.
/// @dev Same canonical Pool ABI across Optimism, Arbitrum, Base and Polygon.
interface IAaveV3Pool {
    /// @notice Executes a single-asset flash loan.
    /// @param receiverAddress The contract receiving the funds (must implement executeOperation).
    /// @param asset The address of the asset being borrowed.
    /// @param amount The amount to borrow.
    /// @param params Arbitrary bytes passed back to executeOperation.
    /// @param referralCode Aave referral code (0 = none).
    function flashLoanSimple(
        address receiverAddress,
        address asset,
        uint256 amount,
        bytes calldata params,
        uint16 referralCode
    ) external;

    /// @notice The total flash-loan premium, expressed in bps of the borrowed amount.
    /// @return The premium (e.g. 5 = 0.05%).
    function FLASHLOAN_PREMIUM_TOTAL() external view returns (uint128);
}

/// @title IAaveFlashLoanSimpleReceiver
/// @notice Callback interface Aave V3 invokes after transferring the funds.
interface IAaveFlashLoanSimpleReceiver {
    /// @notice Called by the Aave Pool mid-flash-loan; must repay by approving `amount + premium`.
    /// @return success Must return true for the pool to complete the loan.
    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external returns (bool success);
}
