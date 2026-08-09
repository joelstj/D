// SPDX-License-Identifier: MIT
pragma solidity 0.8.20;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {OptimalArbitrage} from "../libraries/OptimalArbitrage.sol";
import {DexRouter} from "../libraries/DexRouter.sol";
import {SwapStep} from "../libraries/ArbTypes.sol";
import {IBridgeAdapter} from "../interfaces/IBridgeAdapter.sol";

/// @title OptimalArbitrageHarness
/// @notice Exposes the internal OptimalArbitrage library for unit testing.
contract OptimalArbitrageHarness {
    function optimalV2Amount(uint256 rInA, uint256 rOutA, uint256 rInB, uint256 rOutB, uint256 feeBps)
        external
        pure
        returns (uint256 amountIn, uint256 expectedProfit)
    {
        return OptimalArbitrage.optimalV2Amount(rInA, rOutA, rInB, rOutB, feeBps);
    }

    function optimalV2AmountTwoFee(
        uint256 rInA,
        uint256 rOutA,
        uint256 rInB,
        uint256 rOutB,
        uint256 feeBpsBuy,
        uint256 feeBpsSell
    ) external pure returns (uint256 amountIn, uint256 expectedProfit) {
        return OptimalArbitrage.optimalV2AmountTwoFee(rInA, rOutA, rInB, rOutB, feeBpsBuy, feeBpsSell);
    }

    function getAmountOut(uint256 amountIn, uint256 rIn, uint256 rOut, uint256 feeBps) external pure returns (uint256) {
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
    event Bridged(address token, uint256 amount, uint256 dstChainId, address recipient, bytes options);

    function bridge(address token, uint256 amount, uint256 dstChainId, address recipient, bytes calldata options)
        external
        payable
        override
    {
        IERC20(token).transferFrom(msg.sender, address(this), amount);
        emit Bridged(token, amount, dstChainId, recipient, options);
    }
}

/// @title DexRouterHarness
/// @notice Exposes the internal DexRouter.execute for isolated unit testing
///         of the per-DexType dispatch and (for UNISWAP_V2/UNISWAP_V3_SINGLE)
///         its hand-encoded Yul call paths, without pulling in the whole
///         FlashLoanArbitrage flash-loan flow.
contract DexRouterHarness {
    function execute(SwapStep calldata step, uint256 amountIn, uint256 index) external returns (uint256) {
        return DexRouter.execute(step, amountIn, index);
    }
}
