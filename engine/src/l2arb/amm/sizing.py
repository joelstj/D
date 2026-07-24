"""Optimal trade-size solvers — turn a candidate cycle into its best size.

A margin-profitable cycle is not yet an opportunity: profit as a function of
input size is **concave** (it rises, peaks, then falls as price impact grows), so
there is a single best size ``s*`` (docs/ARBITRAGE_THEORY §4). This module finds
it two ways:

* :func:`optimal_size` — a fully general integer unimodal maximiser over the
  exact profit function ``profit(s) = route_output(s) - s``. Works for any cycle
  (V2, V3, mixed, multi-hop) because it only needs a ``route_output`` callable.
* :func:`two_pool_optimal_input` — the closed-form optimum for the classic
  two-constant-product-pool cycle, used as a fast path and as an independent
  cross-check on the general solver (T-0305).

Everything is integer base units; the solvers evaluate the **exact** AMM math, so
the returned size and profit are the real, on-chain-accurate numbers — never a
float approximation (ADR-005).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import NamedTuple

from l2arb.constants import FEE_DENOMINATOR

__all__ = [
    "SizingResult",
    "maximize_unimodal",
    "optimal_size",
    "optimal_size_auto",
    "two_pool_optimal_input",
]

# Absolute sanity ceiling on the input search (base units) — far beyond any real
# pool, a backstop so the auto-bracket cannot loop unboundedly.
_ABS_INPUT_CAP = 10**36

# A route: given an input size in base units, the exact output after the full
# cycle, in the same (numeraire) token's base units.
RouteOutput = Callable[[int], int]


class SizingResult(NamedTuple):
    """The best input size and the exact net profit (output - input) at it."""

    size: int
    profit: int


# Inverse golden ratio: the interior-point split that lets each step reuse one
# evaluation from the previous step (~1 f-call per iteration vs 2 for ternary).
_INV_PHI = (5**0.5 - 1) / 2  # ~0.618


def maximize_unimodal(f: Callable[[int], int], lo: int, hi: int) -> tuple[int, int]:
    """Return ``(argmax, f(argmax))`` of a unimodal ``f`` over integer ``[lo, hi]``.

    Golden-section search: each step narrows the bracket by the golden ratio
    (~0.618x) while a memo makes the reused interior probe a cache hit, so the cost
    is ~1 new ``f`` call per step — far fewer than a ternary search over the same
    range. The size-search inner loop is the detector's dominant cost, so those
    saved evaluations matter.

    The two interior probes are **recomputed from the live bracket every step** (not
    carried over as coordinates), so integer rounding cannot let them drift and cross
    — at width ``> 4`` a fresh split always keeps them strictly ordered. Below that
    width a final exact integer brute-force pins the true argmax, so correctness
    never rides on the float golden ratio. Assumes one interior peak, which the
    strictly-concave AMM profit curve guarantees.
    """
    if hi < lo:
        raise ValueError(f"empty interval [{lo}, {hi}]")
    memo: dict[int, int] = {}

    def fm(x: int) -> int:
        if x not in memo:
            memo[x] = f(x)
        return memo[x]

    a, b = lo, hi
    # Golden-section down to a small window; probes are recomputed fresh each step so
    # width > 4 guarantees c < d, and the memo turns the carried-over probe into a
    # cache hit rather than a drifting coordinate.
    while b - a > 4:
        c = b - round((b - a) * _INV_PHI)
        d = a + round((b - a) * _INV_PHI)
        if fm(c) < fm(d):  # peak is to the right of c
            a = c
        else:  # peak is to the left of d
            b = d
    best_x = max(range(a, b + 1), key=fm)
    return best_x, fm(best_x)


def optimal_size(route_output: RouteOutput, s_max: int) -> SizingResult:
    """Maximise ``profit(s) = route_output(s) - s`` over ``s in [0, s_max]``.

    Returns the best size and its exact profit. ``s = 0`` (no trade) always
    yields profit 0, so an unprofitable cycle returns ``SizingResult(0, 0)`` and
    the caller's net-profit gate rejects it — the solver never invents an edge.
    """
    if s_max <= 0:
        return SizingResult(0, 0)

    def profit(s: int) -> int:
        return route_output(s) - s

    best_x, best_p = maximize_unimodal(profit, 0, s_max)
    if best_p <= 0:
        return SizingResult(0, 0)
    return SizingResult(best_x, best_p)


def optimal_size_auto(route_output: RouteOutput, seed_hint: int = 1) -> SizingResult:
    """:func:`optimal_size` with an auto-bracketed upper bound.

    Doubles from ``seed_hint`` while profit keeps rising to find an upper bound
    that is guaranteed ``>= `` the concave peak, then hands ``[0, hi]`` to the
    exact solver (which also searches *below* the seed). The caller need not know
    the pools' depth — a rough ``seed_hint`` (e.g. a binding reserve) suffices.
    """
    seed = max(1, seed_hint)
    hi = seed
    best = route_output(hi) - hi
    while hi < _ABS_INPUT_CAP:
        nxt = hi * 2
        profit = route_output(nxt) - nxt
        if profit <= best:
            hi = nxt  # one doubling past the peak brackets it
            break
        best = profit
        hi = nxt
    return optimal_size(route_output, hi)


def two_pool_optimal_input(
    a_reserve_in1: int,
    b_reserve_out1: int,
    fee_pips1: int,
    b_reserve_in2: int,
    a_reserve_out2: int,
    fee_pips2: int,
) -> int:
    """Closed-form optimal input for a two-constant-product-pool cycle A→B→A.

    Pool 1 swaps A→B with oriented reserves ``(a_reserve_in1, b_reserve_out1)``;
    pool 2 swaps B→A with ``(b_reserve_in2, a_reserve_out2)``. Composing the two
    constant-product swaps gives a saturating output ``z(x) = N·x / (C + D·x)``
    with

        ``N = g1·g2·A2·B1``,  ``C = A1·B2``,  ``D = g1·(B2 + g2·B1)``

    (``g = 1 - fee``). Maximising ``z(x) - x`` gives ``x* = (sqrt(N*C) - C) / D``.
    Positive only when ``N > C`` — exactly the "product of marginal rates > 1"
    condition. Returned as a **candidate** integer size; callers refine and
    re-price it with the exact integer solver (it ignores integer rounding).
    """
    g1 = (FEE_DENOMINATOR - fee_pips1) / FEE_DENOMINATOR
    g2 = (FEE_DENOMINATOR - fee_pips2) / FEE_DENOMINATOR
    big_a1, big_b1 = float(a_reserve_in1), float(b_reserve_out1)
    big_b2, big_a2 = float(b_reserve_in2), float(a_reserve_out2)
    numerator = g1 * g2 * big_a2 * big_b1
    constant = big_a1 * big_b2
    if numerator <= constant:  # no positive-profit size at the margin
        return 0
    denominator = g1 * (big_b2 + g2 * big_b1)
    x_star = (math.sqrt(numerator * constant) - constant) / denominator
    return max(0, round(x_star))
