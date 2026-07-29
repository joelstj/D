// SPDX-License-Identifier: MIT
pragma solidity 0.8.20;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";

import {SwapStep, DexType} from "./ArbTypes.sol";
import {IUniswapV2Router} from "../interfaces/dex/IUniswapV2.sol";
import {ISwapRouter02} from "../interfaces/dex/IUniswapV3.sol";
import {ICurvePool} from "../interfaces/dex/ICurvePool.sol";

/// @title DexRouter
/// @notice Internal library that executes a single arbitrage hop against any
///         supported DEX shape and returns the measured output amount.
/// @dev All functions are `internal` and inlined into the calling contract, so
///      there is no external delegatecall surface. The output of each hop is
///      always measured as the *balance delta* of `tokenOut`, which is robust
///      across every venue and makes the engine agnostic to router return-value
///      quirks. Fee-on-transfer / rebasing tokens are intentionally NOT
///      supported (the balance-delta accounting would misprice them).
library DexRouter {
    using SafeERC20 for IERC20;

    error SwapCallFailed(uint256 stepIndex);
    error InsufficientHopOutput(uint256 got, uint256 minOut);
    error BadCalldataOffset();

    /// @notice Approves and executes one hop, returning the amount of tokenOut received.
    /// @param step The hop definition.
    /// @param amountIn The exact amount of `step.tokenIn` to spend.
    /// @param index The hop index (for error reporting only).
    /// @return amountOut The measured increase in `step.tokenOut` balance.
    function execute(SwapStep memory step, uint256 amountIn, uint256 index) internal returns (uint256 amountOut) {
        // Approve exactly what this hop spends (forceApprove tolerates tokens
        // like USDT that require the allowance to be zeroed first).
        IERC20(step.tokenIn).forceApprove(step.router, amountIn);

        uint256 balBefore = balanceOf(step.tokenOut, address(this));

        if (step.dexType == DexType.UNISWAP_V2) {
            address[] memory path = new address[](2);
            path[0] = step.tokenIn;
            path[1] = step.tokenOut;
            IUniswapV2Router(step.router)
                .swapExactTokensForTokens(amountIn, step.minOut, path, address(this), block.timestamp);
        } else if (step.dexType == DexType.UNISWAP_V3_SINGLE) {
            ISwapRouter02(step.router)
                .exactInputSingle(
                    ISwapRouter02.ExactInputSingleParams({
                    tokenIn: step.tokenIn,
                    tokenOut: step.tokenOut,
                    fee: step.poolFee,
                    recipient: address(this),
                    amountIn: amountIn,
                    amountOutMinimum: step.minOut,
                    sqrtPriceLimitX96: 0
                })
                );
        } else if (step.dexType == DexType.UNISWAP_V3_MULTI) {
            ISwapRouter02(step.router)
                .exactInput(
                    ISwapRouter02.ExactInputParams({
                    path: step.data, recipient: address(this), amountIn: amountIn, amountOutMinimum: step.minOut
                })
                );
        } else if (step.dexType == DexType.CURVE) {
            // Low-level call: some Curve pools' `exchange` returns nothing.
            (bool ok,) = step.router
                .call(
                    abi.encodeWithSelector(
                        ICurvePool.exchange.selector, step.curveI, step.curveJ, amountIn, step.minOut
                    )
                );
            if (!ok) revert SwapCallFailed(index);
        } else {
            // GENERIC: raw calldata, optionally patched with the runtime amountIn.
            bytes memory data = step.data;
            uint256 offset = step.amountInOffset;
            if (offset != 0) {
                if (offset + 32 > data.length) revert BadCalldataOffset();
                assembly {
                    // data points to length word; payload starts at data+0x20.
                    mstore(add(add(data, 0x20), offset), amountIn)
                }
            }
            (bool ok,) = step.router.call(data);
            if (!ok) revert SwapCallFailed(index);
        }

        uint256 balAfter = balanceOf(step.tokenOut, address(this));
        amountOut = balAfter - balBefore;

        // Per-hop slippage floor (typed routers also enforce their own, but for
        // CURVE/GENERIC this is the primary guard).
        if (step.minOut != 0 && amountOut < step.minOut) {
            revert InsufficientHopOutput(amountOut, step.minOut);
        }
    }

    /// @notice Gas-lean ERC20 balanceOf via a single staticcall in Yul.
    /// @param token The token to query.
    /// @param account The holder.
    /// @return bal The token balance of `account`.
    function balanceOf(address token, address account) internal view returns (uint256 bal) {
        assembly {
            let ptr := mload(0x40)
            // balanceOf(address) selector = 0x70a08231
            mstore(ptr, 0x70a0823100000000000000000000000000000000000000000000000000000000)
            mstore(add(ptr, 0x04), account)
            let ok := staticcall(gas(), token, ptr, 0x24, 0x00, 0x20)
            if or(iszero(ok), lt(returndatasize(), 0x20)) {
                returndatacopy(0x00, 0x00, returndatasize())
                revert(0x00, returndatasize())
            }
            bal := mload(0x00)
        }
    }
}
