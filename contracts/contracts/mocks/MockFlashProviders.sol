// SPDX-License-Identifier: MIT
pragma solidity 0.8.20;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {IAaveFlashLoanSimpleReceiver} from "../interfaces/IAaveV3Pool.sol";
import {IBalancerFlashLoanRecipient} from "../interfaces/IBalancerVault.sol";

/// @title MockAavePool
/// @notice Minimal Aave V3 Pool that lends its own balance and pulls back
///         `amount + premium` via allowance, mirroring the real callback flow.
contract MockAavePool {
    uint128 public premiumBps;

    error NotRepaid();
    error CallbackFailed();

    constructor(uint128 premiumBps_) {
        premiumBps = premiumBps_; // e.g. 5 = 0.05%
    }

    function FLASHLOAN_PREMIUM_TOTAL() external view returns (uint128) {
        return premiumBps;
    }

    function flashLoanSimple(
        address receiverAddress,
        address asset,
        uint256 amount,
        bytes calldata params,
        uint16 /*referralCode*/
    )
        external
    {
        uint256 premium = (amount * premiumBps) / 10_000;
        uint256 balBefore = IERC20(asset).balanceOf(address(this));

        IERC20(asset).transfer(receiverAddress, amount);
        bool ok =
            IAaveFlashLoanSimpleReceiver(receiverAddress).executeOperation(asset, amount, premium, msg.sender, params);
        if (!ok) revert CallbackFailed();

        // Pull principal + premium back (receiver approved us).
        IERC20(asset).transferFrom(receiverAddress, address(this), amount + premium);
        if (IERC20(asset).balanceOf(address(this)) < balBefore + premium) revert NotRepaid();
    }
}

/// @title MockBalancerVault
/// @notice Minimal Balancer V2 Vault that lends (0 fee) and requires the
///         recipient to transfer the funds back within the callback.
contract MockBalancerVault {
    error NotRepaid();

    function flashLoan(
        address recipient,
        address[] calldata tokens,
        uint256[] calldata amounts,
        bytes calldata userData
    ) external {
        uint256[] memory fees = new uint256[](tokens.length);
        uint256[] memory balBefore = new uint256[](tokens.length);

        for (uint256 i; i < tokens.length; ++i) {
            balBefore[i] = IERC20(tokens[i]).balanceOf(address(this));
            IERC20(tokens[i]).transfer(recipient, amounts[i]);
        }

        IBalancerFlashLoanRecipient(recipient).receiveFlashLoan(tokens, amounts, fees, userData);

        for (uint256 i; i < tokens.length; ++i) {
            if (IERC20(tokens[i]).balanceOf(address(this)) < balBefore[i] + fees[i]) {
                revert NotRepaid();
            }
        }
    }
}
