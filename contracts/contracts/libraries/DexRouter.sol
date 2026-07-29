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
///
///      Yul scope (house style — see contracts/CLAUDE.md: "Yul... on the hot
///      path... only where it measurably wins"): UNISWAP_V2 and
///      UNISWAP_V3_SINGLE hand-encode their call in raw assembly (see
///      `_swapUniswapV2`/`_swapUniswapV3Single`), skipping the ABI-encoder's
///      memory allocation for the fixed-shape calls this engine always makes
///      (a static 2-element path; a fully static params struct). Selectors
///      come from the imported interfaces' `.selector` — a compile-time
///      constant — rather than a hand-typed hex literal, so a wrong signature
///      is a compile error, not a runtime guess. CURVE and GENERIC were
///      already raw low-level calls (the caller supplies the calldata, or a
///      fixed 4-arg selector). UNISWAP_V3_MULTI keeps the typed call: its
///      `bytes path` parameter is variable-length, and hand-rolling dynamic-
///      bytes ABI encoding for a comparatively cold path (multi-hop V3) isn't
///      worth the added risk for the gas it would save. The branch
///      *selection* itself (if/else over a 5-way enum) is left to the
///      Solidity optimizer (viaIR, already on): it already compiles a small
///      enum dispatch efficiently, so a hand-written Yul `switch` here
///      wouldn't measurably win — and "measurably wins" is the bar.
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
            _swapUniswapV2(step.router, amountIn, step.minOut, step.tokenIn, step.tokenOut);
        } else if (step.dexType == DexType.UNISWAP_V3_SINGLE) {
            _swapUniswapV3Single(step.router, amountIn, step.minOut, step.tokenIn, step.tokenOut, step.poolFee);
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

    /// @dev Hand-encodes and calls `swapExactTokensForTokens(amountIn, minOut,
    ///      [tokenIn, tokenOut], address(this), block.timestamp)` — the fixed
    ///      2-element-path shape this engine always uses. Layout (selector +
    ///      8 words; the dynamic `path` array's offset is always 0xa0 since
    ///      it's the 3rd of 5 top-level params, i.e. 5 head slots x 32 bytes):
    ///        0x00 selector             0x64 to (address(this))
    ///        0x04 amountIn             0x84 deadline (block.timestamp)
    ///        0x24 amountOutMin         0xa4 path.length (2)
    ///        0x44 path offset (0xa0)   0xc4 path[0] (tokenIn)
    ///                                  0xe4 path[1] (tokenOut)
    ///      On failure, bubbles the router's own revert data (more useful for
    ///      off-chain simulation than a generic error) instead of swallowing
    ///      it into SwapCallFailed.
    function _swapUniswapV2(address router, uint256 amountIn, uint256 minOut, address tokenIn, address tokenOut)
        private
    {
        bytes4 selector = IUniswapV2Router.swapExactTokensForTokens.selector;
        assembly {
            let ptr := mload(0x40)
            mstore(ptr, selector)
            mstore(add(ptr, 0x04), amountIn)
            mstore(add(ptr, 0x24), minOut)
            mstore(add(ptr, 0x44), 0xa0)
            mstore(add(ptr, 0x64), address())
            mstore(add(ptr, 0x84), timestamp())
            mstore(add(ptr, 0xa4), 2)
            mstore(add(ptr, 0xc4), tokenIn)
            mstore(add(ptr, 0xe4), tokenOut)
            if iszero(call(gas(), router, 0, ptr, 0x104, 0x00, 0x00)) {
                returndatacopy(ptr, 0x00, returndatasize())
                revert(ptr, returndatasize())
            }
        }
    }

    /// @dev Hand-encodes and calls `exactInputSingle` with the params struct
    ///      this engine always uses (recipient = address(this),
    ///      sqrtPriceLimitX96 = 0). The struct is fully static (address,
    ///      address, uint24, address, uint256, uint256, uint160 — no dynamic
    ///      members), so its ABI encoding is 7 words back-to-back with no
    ///      offset/length header. Layout (selector + 7 words):
    ///        0x00 selector    0x64 recipient (address(this))
    ///        0x04 tokenIn     0x84 amountIn
    ///        0x24 tokenOut    0xa4 amountOutMinimum
    ///        0x44 fee         0xc4 sqrtPriceLimitX96 (0)
    ///      On failure, bubbles the router's own revert data (see
    ///      `_swapUniswapV2`).
    function _swapUniswapV3Single(
        address router,
        uint256 amountIn,
        uint256 minOut,
        address tokenIn,
        address tokenOut,
        uint24 fee
    ) private {
        bytes4 selector = ISwapRouter02.exactInputSingle.selector;
        assembly {
            let ptr := mload(0x40)
            mstore(ptr, selector)
            mstore(add(ptr, 0x04), tokenIn)
            mstore(add(ptr, 0x24), tokenOut)
            mstore(add(ptr, 0x44), fee)
            mstore(add(ptr, 0x64), address())
            mstore(add(ptr, 0x84), amountIn)
            mstore(add(ptr, 0xa4), minOut)
            mstore(add(ptr, 0xc4), 0)
            if iszero(call(gas(), router, 0, ptr, 0xe4, 0x00, 0x00)) {
                returndatacopy(ptr, 0x00, returndatasize())
                revert(ptr, returndatasize())
            }
        }
    }
}
