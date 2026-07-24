// SPDX-License-Identifier: MIT
pragma solidity 0.8.20;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";

/// @title MockUniV2
/// @notice A minimal constant-product AMM that doubles as both a UniswapV2
///         "pair" (getReserves/token0/token1) and "router"
///         (swapExactTokensForTokens), so a single instance can back both the
///         optimal-sizing quoter and the swap route in tests.
/// @dev Reserves are simply this contract's live token balances. Prices are set
///      by funding it with an imbalanced ratio.
contract MockUniV2 {
    address public immutable token0;
    address public immutable token1;
    uint256 public immutable feeBps; // e.g. 30 = 0.30%

    error IdenticalTokens();
    error InvalidPath();
    error InsufficientOutput();

    constructor(address tokenA, address tokenB, uint256 feeBps_) {
        if (tokenA == tokenB) revert IdenticalTokens();
        // Sort like Uniswap so token0 < token1.
        (token0, token1) = tokenA < tokenB ? (tokenA, tokenB) : (tokenB, tokenA);
        feeBps = feeBps_;
    }

    /// @notice UniswapV2-style reserves (live balances of token0/token1).
    function getReserves()
        external
        view
        returns (uint112 reserve0, uint112 reserve1, uint32 blockTimestampLast)
    {
        reserve0 = uint112(IERC20(token0).balanceOf(address(this)));
        reserve1 = uint112(IERC20(token1).balanceOf(address(this)));
        blockTimestampLast = 0;
    }

    /// @notice Constant-product output for a given input.
    function getAmountOut(uint256 amountIn, uint256 reserveIn, uint256 reserveOut)
        public
        view
        returns (uint256)
    {
        uint256 amountInWithFee = amountIn * (10_000 - feeBps);
        return (amountInWithFee * reserveOut) / (reserveIn * 10_000 + amountInWithFee);
    }

    /// @notice UniswapV2-compatible swap (single hop; path length must be 2).
    function swapExactTokensForTokens(
        uint256 amountIn,
        uint256 amountOutMin,
        address[] calldata path,
        address to,
        uint256 /*deadline*/
    ) external returns (uint256[] memory amounts) {
        if (path.length != 2) revert InvalidPath();
        address tokenIn = path[0];
        address tokenOut = path[1];

        uint256 reserveIn = IERC20(tokenIn).balanceOf(address(this));
        uint256 reserveOut = IERC20(tokenOut).balanceOf(address(this));
        uint256 amountOut = getAmountOut(amountIn, reserveIn, reserveOut);
        if (amountOut < amountOutMin) revert InsufficientOutput();

        IERC20(tokenIn).transferFrom(msg.sender, address(this), amountIn);
        IERC20(tokenOut).transfer(to, amountOut);

        amounts = new uint256[](2);
        amounts[0] = amountIn;
        amounts[1] = amountOut;
    }
}
