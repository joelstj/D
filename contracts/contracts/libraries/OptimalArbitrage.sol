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
///      That form assumes BOTH hops charge the same fee. When they differ (very
///      common — a 0.30% V2 pool quoted against a 0.05% V3-style pool) the
///      general solution keeps each hop's own multiplier a1 = (1 - fee1),
///      a2 = (1 - fee2):
///
///        dx* = sqrt(a1*a2*Xa*Ya*Yb*Xb) - Xa*Yb
///              ---------------------------------
///                    a1 * (Yb + a2*Ya)
///
///      which collapses to the single-fee form above when a1 == a2, so it is a
///      generalisation of it rather than a competing derivation.
///      `optimalV2AmountTwoFee` implements this and `optimalV2Amount` delegates
///      to it, keeping one implementation of the math.
///
///      This is an on-chain *advisory*: the geometric factor is computed with an
///      integer square root, so the result can be a unit or two off the exact
///      real-valued optimum. The atomic on-chain minProfit guard in
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
        return optimalV2AmountTwoFee(reserveInA, reserveOutA, reserveInB, reserveOutB, feeBps, feeBps);
    }

    /// @notice Optimal input amount when the two pools charge DIFFERENT fees.
    /// @param reserveInA  Reserve of the borrowed asset X in the buy pool A.
    /// @param reserveOutA Reserve of the intermediate token Y in the buy pool A.
    /// @param reserveInB  Reserve of the intermediate token Y in the sell pool B.
    /// @param reserveOutB Reserve of the borrowed asset X in the sell pool B.
    /// @param feeBpsBuy   Swap fee (bps) charged by pool A.
    /// @param feeBpsSell  Swap fee (bps) charged by pool B.
    /// @return amountIn       Optimal amount of X to borrow (0 if no profitable arb).
    /// @return expectedProfit Expected profit in X at that size (0 if none).
    /// @dev Implements the two-fee closed form documented on this library. With
    ///      `a1 = BPS - feeBpsBuy` and `a2 = BPS - feeBpsSell` (both scaled by
    ///      BPS), clearing denominators gives the all-integer form used below:
    ///
    ///        amountIn = BPS * (sqrt(a1*a2*Xa*Ya*Yb*Xb) - BPS*Xa*Yb)
    ///                   ---------------------------------------------
    ///                            a1 * (BPS*Yb + a2*Ya)
    ///
    ///      Passing the same fee twice reproduces the previous single-fee result
    ///      exactly (verified by test), so this is a strict generalisation.
    function optimalV2AmountTwoFee(
        uint256 reserveInA,
        uint256 reserveOutA,
        uint256 reserveInB,
        uint256 reserveOutB,
        uint256 feeBpsBuy,
        uint256 feeBpsSell
    ) internal pure returns (uint256 amountIn, uint256 expectedProfit) {
        if (reserveInA == 0 || reserveOutA == 0 || reserveInB == 0 || reserveOutB == 0) {
            return (0, 0);
        }
        if (feeBpsBuy >= BPS || feeBpsSell >= BPS) return (0, 0);

        uint256 a1 = BPS - feeBpsBuy;
        uint256 a2 = BPS - feeBpsSell;

        // root = sqrt(a1*a2*Xa*Ya*Yb*Xb), split into two square roots so each
        // operand stays well inside uint256: sqrt(a1*a2*Xa*Ya) * sqrt(Yb*Xb).
        // a1*a2 <= BPS^2 = 1e8, so this only costs 1e8 of headroom against the
        // ~1e38-per-pair ceiling the single-fee form already documented — real
        // L2 pool reserves are orders of magnitude below that. Callers holding
        // reserves near the ceiling should down-scale before quoting.
        uint256 root = sqrt(a1 * a2 * reserveInA * reserveOutA) * sqrt(reserveInB * reserveOutB);

        uint256 rhs = BPS * reserveInA * reserveInB;
        if (root <= rhs) {
            // Price on B does not exceed the fee-adjusted price on A: no arb.
            return (0, 0);
        }

        uint256 numerator = BPS * (root - rhs);
        uint256 denominator = a1 * (BPS * reserveInB + a2 * reserveOutA);
        amountIn = numerator / denominator;
        if (amountIn == 0) return (0, 0);

        uint256 out1 = getAmountOut(amountIn, reserveInA, reserveOutA, feeBpsBuy);
        uint256 out2 = getAmountOut(out1, reserveInB, reserveOutB, feeBpsSell);
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
