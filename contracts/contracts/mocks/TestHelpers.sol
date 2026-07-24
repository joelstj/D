// SPDX-License-Identifier: MIT
pragma solidity 0.8.20;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {OptimalArbitrage} from "../libraries/OptimalArbitrage.sol";
import {IBridgeAdapter} from "../interfaces/IBridgeAdapter.sol";

/// @title OptimalArbitrageHarness
/// @notice Exposes the internal OptimalArbitrage library for unit testing.
contract OptimalArbitrageHarness {
    function optimalV2Amount(
        uint256 rInA,
        uint256 rOutA,
        uint256 rInB,
        uint256 rOutB,
        uint256 feeBps
    ) external pure returns (uint256 amountIn, uint256 expectedProfit) {
        return OptimalArbitrage.optimalV2Amount(rInA, rOutA, rInB, rOutB, feeBps);
    }

    function getAmountOut(uint256 amountIn, uint256 rIn, uint256 rOut, uint256 feeBps)
        external
        pure
        returns (uint256)
    {
        return OptimalArbitrage.getAmountOut(amountIn, rIn, rOut, feeBps);
    }

    function sqrt(uint256 x) external pure returns (uint256) {
        return OptimalArbitrage.sqrt(x);
    }
}

/// @title MockBridgeAdapter
/// @notice Test double that "bridges" by pulling the token and holding it,
///         emitting an event a test harness can assert on.
contract MockBridgeAdapter is IBridgeAdapter {
    event Bridged(
        address token, uint256 amount, uint256 dstChainId, address recipient, bytes options
    );

    function bridge(
        address token,
        uint256 amount,
        uint256 dstChainId,
        address recipient,
        bytes calldata options
    ) external payable override {
        IERC20(token).transferFrom(msg.sender, address(this), amount);
        emit Bridged(token, amount, dstChainId, recipient, options);
    }
}
