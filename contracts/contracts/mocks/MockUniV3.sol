// SPDX-License-Identifier: MIT
pragma solidity 0.8.20;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";

/// @title MockUniV3Router
/// @notice A minimal constant-product pool that implements the SwapRouter02
///         `exactInputSingle` surface, so offline tests can exercise
///         DexType.UNISWAP_V3_SINGLE without a live fork.
/// @dev Reserves are simply this contract's live token balances, exactly like
///      MockUniV2 — it just exposes the V3-shaped call instead of the V2 one.
///      `fee` and `sqrtPriceLimitX96` are accepted (matching the real ABI) but
///      ignored: the mock doesn't model concentrated liquidity or fee tiers.
contract MockUniV3Router {
    uint256 public immutable feeBps; // e.g. 5 = 0.05%

    error InsufficientOutput();

    struct ExactInputSingleParams {
        address tokenIn;
        address tokenOut;
        uint24 fee;
        address recipient;
        uint256 amountIn;
        uint256 amountOutMinimum;
        uint160 sqrtPriceLimitX96;
    }

    constructor(uint256 feeBps_) {
        feeBps = feeBps_;
    }

    /// @notice Constant-product output for a given input (mirrors MockUniV2).
    function getAmountOut(uint256 amountIn, uint256 reserveIn, uint256 reserveOut) public view returns (uint256) {
        uint256 amountInWithFee = amountIn * (10_000 - feeBps);
        return (amountInWithFee * reserveOut) / (reserveIn * 10_000 + amountInWithFee);
    }

    function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
        uint256 reserveIn = IERC20(params.tokenIn).balanceOf(address(this));
        uint256 reserveOut = IERC20(params.tokenOut).balanceOf(address(this));
        amountOut = getAmountOut(params.amountIn, reserveIn, reserveOut);
        if (amountOut < params.amountOutMinimum) revert InsufficientOutput();

        IERC20(params.tokenIn).transferFrom(msg.sender, address(this), params.amountIn);
        IERC20(params.tokenOut).transfer(params.recipient, amountOut);
    }
}
