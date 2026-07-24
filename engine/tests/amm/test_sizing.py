"""Unit tests for the optimal-size solvers.

Covers the general integer unimodal maximiser, the profit-maximising
``optimal_size`` over an exact route, and the agreement between the closed-form
two-pool optimum and the general solver (T-0305).
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from l2arb.amm import constant_product as cp
from l2arb.amm import sizing

pytestmark = pytest.mark.unit


@settings(max_examples=400, deadline=None)
@given(lo=st.integers(0, 2000), width=st.integers(0, 3000), peak=st.integers(-1000, 6000))
def test_golden_section_matches_brute_force(lo: int, width: int, peak: int) -> None:
    # Strictly-concave unimodal f: golden-section must find the exact integer argmax
    # a brute-force scan would, whether the peak is inside the range or outside it.
    hi = lo + width

    def f(x: int) -> int:
        return -((x - peak) ** 2)

    gx, gv = sizing.maximize_unimodal(f, lo, hi)
    bx = max(range(lo, hi + 1), key=f)
    assert gx == bx
    assert gv == f(bx)


def test_maximize_unimodal_finds_the_peak() -> None:
    # Concave parabola peaking at x = 50.
    peak, value = sizing.maximize_unimodal(lambda x: -((x - 50) ** 2), 0, 100)
    assert peak == 50
    assert value == 0


def test_maximize_unimodal_small_and_degenerate_intervals() -> None:
    assert sizing.maximize_unimodal(lambda x: -x, 0, 0) == (0, 0)
    assert sizing.maximize_unimodal(lambda x: -abs(x - 2), 0, 2) == (2, 0)
    with pytest.raises(ValueError, match="empty interval"):
        sizing.maximize_unimodal(lambda x: x, 5, 4)


def _two_pool_route(
    a_in1: int, b_out1: int, fee1: int, b_in2: int, a_out2: int, fee2: int
) -> sizing.RouteOutput:
    """Exact A→B→A output as a function of input A (chained V2 swaps)."""

    def route(x: int) -> int:
        y = cp.amount_out_for_in(a_in1, b_out1, x, fee1)
        return cp.amount_out_for_in(b_in2, a_out2, y, fee2)

    return route


def test_optimal_size_on_a_profitable_cycle() -> None:
    # Pool1 ~1:1, pool2 pays 1.1 A per B -> ~9 % marginal edge.
    a1 = b1 = b2 = 10**21
    a2 = 11 * 10**20
    route = _two_pool_route(a1, b1, 3000, b2, a2, 3000)
    result = sizing.optimal_size(route, s_max=5 * 10**20)
    assert result.size > 0
    assert result.profit > 0
    # The reported profit is exactly route(size) - size.
    assert result.profit == route(result.size) - result.size
    # It is a genuine maximum: neighbours do not beat it.
    assert route(result.size) - result.size >= route(result.size + 10**15) - (result.size + 10**15)


def test_optimal_size_on_an_unprofitable_cycle_returns_zero() -> None:
    # Both pools balanced 1:1 with fees -> round trip always loses.
    a = 10**21
    route = _two_pool_route(a, a, 3000, a, a, 3000)
    result = sizing.optimal_size(route, s_max=10**20)
    assert result == sizing.SizingResult(0, 0)


def test_optimal_size_zero_smax() -> None:
    assert sizing.optimal_size(lambda x: x * 2, 0) == sizing.SizingResult(0, 0)


def test_closed_form_matches_general_solver() -> None:
    # T-0305: the two independent solvers must agree.
    a1 = b1 = b2 = 10**21
    a2 = 11 * 10**20
    closed = sizing.two_pool_optimal_input(a1, b1, 3000, b2, a2, 3000)
    route = _two_pool_route(a1, b1, 3000, b2, a2, 3000)
    general = sizing.optimal_size(route, s_max=5 * 10**20)

    assert closed > 0
    # Sizes agree within 0.5 % (closed form ignores integer rounding).
    assert abs(closed - general.size) <= general.size // 200 + 1
    # The general solver is exact, so its profit is at least the closed form's.
    assert general.profit >= route(closed) - closed


def test_closed_form_returns_zero_without_an_edge() -> None:
    a = 10**21
    assert sizing.two_pool_optimal_input(a, a, 3000, a, a, 3000) == 0


def test_optimal_size_auto_matches_bounded_solver() -> None:
    a1 = b1 = b2 = 10**21
    a2 = 11 * 10**20
    route = _two_pool_route(a1, b1, 3000, b2, a2, 3000)
    auto = sizing.optimal_size_auto(route, seed_hint=a1)
    bounded = sizing.optimal_size(route, s_max=5 * 10**20)
    assert auto.size > 0
    # Auto-bracketing brackets the same peak as the manually-bounded search.
    assert abs(auto.size - bounded.size) <= bounded.size // 100 + 1


def test_optimal_size_auto_brackets_optimum_above_seed() -> None:
    # Extreme edge: the optimum exceeds the first pool's reserve, so a naive
    # reserve-sized bound would miss it — the auto-bracket must climb past it.
    a1 = b1 = b2 = 100 * 10**18
    a2 = 10_000 * 10**18
    route = _two_pool_route(a1, b1, 3000, b2, a2, 3000)
    auto = sizing.optimal_size_auto(route, seed_hint=a1)
    assert auto.profit > 0
    # Beating the reserve-bounded search proves the bracket climbed past reserve.
    reserve_bounded = sizing.optimal_size(route, s_max=a1)
    assert auto.profit >= reserve_bounded.profit


def test_optimal_size_auto_unprofitable_returns_zero() -> None:
    a = 10**21
    route = _two_pool_route(a, a, 3000, a, a, 3000)
    assert sizing.optimal_size_auto(route, seed_hint=a) == sizing.SizingResult(0, 0)


def test_optimal_size_auto_terminates_at_absolute_cap() -> None:
    # A pathological route whose profit never stops rising must still terminate
    # (bounded by the absolute input cap) rather than loop forever.
    result = sizing.optimal_size_auto(lambda s: s * 2, seed_hint=1)
    assert result.size > 0
