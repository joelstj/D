// SPDX-License-Identifier: MIT
pragma solidity 0.8.20;

/// @title OptimalArbitrage
/// @notice Closed-form optimal loan sizing for two-hop arbitrage between two
///         constant-product (UniswapV2-style) pools.
/// @dev Given a "buy" pool A where the borrowed asset X is spent for token Y,
///      and a "sell" pool B where Y is sold back for X, the profit-maximising
///      input amount has a closed form. With fee multiplier a = (1 - fee):
///
///        dx* = feeDen * (feeNum*k - feeDen*Xa*Yb)
///              -----------------------------------------
///              feeNum * (feeDen*Yb + feeNum*Ya)
///
///      where k = sqrt(Xa*Ya*Yb*Xb), (Xa,Ya) are pool A reserves of (X,Y) and
///      (Yb,Xb) are pool B reserves of (Y,X). Derivation: maximise
///      P(dx) = getAmountOut(getAmountOut(dx, A), B) - dx over dx and solve
///      dP/dx = 0. See docs/ARCHITECTURE.md for the full derivation.
///
///      This is an on-chain *advisory*: the geometric factor k is computed with
///      an integer square root, and when the two pools have different fees the
///      sizing is a close approximation. The atomic on-chain minProfit guard in
///      FlashLoanArbitrage — not this estimate — is the real safety net.
///
///      Yul scope: `sqrt` (below) and `getAmountOut` are hand-optimised — both
///      are the arithmetic leaves called on every quote. `optimalV2Amount`'s
///      own orchestration (early-return guards, the k/lhs/rhs comparison) is
///      deliberately left in checked Solidity: it runs once per quote (not a
///      tight loop), it is only ever reached via an off-chain `eth_call` or
///      this library's own callers — never inside FlashLoanArbitrage's actual
///      borrow/swap/repay path — and its multi-step comparisons are exactly
///      the kind of logic where Solidity's default overflow checks are worth
///      more than the marginal gas they'd save. "Yul only where it measurably
///      wins" (contracts/CLAUDE.md) argues for stopping at the leaves here.
library OptimalArbitrage {
    uint256 internal constant BPS = 10_000;

    /// @notice Computes the profit-maximising input amount and expected profit.
    /// @param reserveInA  Reserve of the borrowed asset X in the buy pool A.
    /// @param reserveOutA Reserve of the intermediate token Y in the buy pool A.
    /// @param reserveInB  Reserve of the intermediate token Y in the sell pool B.
    /// @param reserveOutB Reserve of the borrowed asset X in the sell pool B.
    /// @param feeBps      Swap fee in bps applied on both hops (e.g. 30 = 0.30%).
    /// @return amountIn       Optimal amount of X to borrow (0 if no profitable arb).
    /// @return expectedProfit Expected profit in X at that size (0 if none).
    function optimalV2Amount(
        uint256 reserveInA,
        uint256 reserveOutA,
        uint256 reserveInB,
        uint256 reserveOutB,
        uint256 feeBps
    ) internal pure returns (uint256 amountIn, uint256 expectedProfit) {
        if (reserveInA == 0 || reserveOutA == 0 || reserveInB == 0 || reserveOutB == 0) {
            return (0, 0);
        }
        if (feeBps >= BPS) return (0, 0);

        uint256 feeNum = BPS - feeBps; // a's numerator
        uint256 feeDen = BPS; // a's denominator

        // k = sqrt(Xa*Ya*Yb*Xb), computed overflow-safe as sqrt(Xa*Ya)*sqrt(Yb*Xb).
        // Each pairwise product must fit in uint256; callers with reserves above
        // ~1e38 per pair should down-scale. (Real L2 pools are far below this.)
        uint256 k = sqrt(reserveInA * reserveOutA) * sqrt(reserveInB * reserveOutB);

        uint256 lhs = feeNum * k;
        uint256 rhs = feeDen * reserveInA * reserveInB;
        if (lhs <= rhs) {
            // Price on B does not exceed the fee-adjusted price on A: no arb.
            return (0, 0);
        }

        uint256 numerator = feeDen * (lhs - rhs);
        uint256 denominator = feeNum * (feeDen * reserveInB + feeNum * reserveOutA);
        amountIn = numerator / denominator;
        if (amountIn == 0) return (0, 0);

        uint256 out1 = getAmountOut(amountIn, reserveInA, reserveOutA, feeBps);
        uint256 out2 = getAmountOut(out1, reserveInB, reserveOutB, feeBps);
        expectedProfit = out2 > amountIn ? out2 - amountIn : 0;
    }

    /// @notice Constant-product output for `amountIn`, with a bps fee on input.
    /// @dev Mirrors UniswapV2Library.getAmountOut. Yul-optimised: this is the
    ///      arithmetic core of the library (called from `optimalV2Amount`
    ///      twice per quote, and exposed directly for callers pricing a single
    ///      hop), so it drops Solidity's default checked-arithmetic overhead.
    ///      Safe because `reserveIn` is guarded non-zero above, so
    ///      `denominator = reserveIn*BPS + amountInWithFee` can never be zero
    ///      (Yul's `div` silently returns 0 on a zero denominator rather than
    ///      reverting, unlike Solidity's checked division — there is no such
    ///      path here). Overflow is bounded the same way the rest of this
    ///      library already documents: real pool reserves/amounts are far
    ///      below the ~1e38-per-operand ceiling where a uint256 product could
    ///      wrap (see `optimalV2Amount`'s `k` comment) — callers with
    ///      reserves above that should down-scale first.
    function getAmountOut(uint256 amountIn, uint256 reserveIn, uint256 reserveOut, uint256 feeBps)
        internal
        pure
        returns (uint256 amountOut)
    {
        if (amountIn == 0 || reserveIn == 0 || reserveOut == 0 || feeBps >= BPS) return 0;
        assembly {
            let amountInWithFee := mul(amountIn, sub(BPS, feeBps))
            let numerator := mul(amountInWithFee, reserveOut)
            let denominator := add(mul(reserveIn, BPS), amountInWithFee)
            amountOut := div(numerator, denominator)
        }
    }

    /// @notice Integer square root (floor), Yul-optimised.
    /// @dev Battle-tested branch-and-Newton implementation (solmate/solady
    ///      lineage). Returns the largest z such that z*z <= x.
    function sqrt(uint256 x) internal pure returns (uint256 z) {
        assembly {
            // Compute a first-guess power-of-two near sqrt(x) by scaling 181
            // (approx sqrt of the top of a byte) up through the magnitude of x.
            z := 181

            let y := x
            if iszero(lt(y, 0x10000000000000000000000000000000000)) {
                y := shr(128, y)
                z := shl(64, z)
            }
            if iszero(lt(y, 0x1000000000000000000)) {
                y := shr(64, y)
                z := shl(32, z)
            }
            if iszero(lt(y, 0x10000000000)) {
                y := shr(32, y)
                z := shl(16, z)
            }
            if iszero(lt(y, 0x1000000)) {
                y := shr(16, y)
                z := shl(8, z)
            }

            // z is now within a factor ~2.5 of sqrt(x); refine with Newton's
            // method. Each step roughly doubles the correct bits; 7 steps are
            // sufficient across the whole uint256 range.
            z := shr(18, mul(z, add(y, 65536)))
            z := shr(1, add(z, div(x, z)))
            z := shr(1, add(z, div(x, z)))
            z := shr(1, add(z, div(x, z)))
            z := shr(1, add(z, div(x, z)))
            z := shr(1, add(z, div(x, z)))
            z := shr(1, add(z, div(x, z)))
            z := shr(1, add(z, div(x, z)))

            // Newton may overshoot by one; correct the floor.
            let zRoundDown := div(x, z)
            if lt(zRoundDown, z) { z := zRoundDown }
        }
    }
}
