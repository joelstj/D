"""Unit + property tests for exact concentrated-liquidity (V3) swap math.

Without a live QuoterV2 in the unit tier (that cross-check lives in the
``chain``/``verify`` tiers, T-0304), these tests pin the *laws* and the exact
rounding directions: correct price movement, monotonicity, no-free-lunch on a
round trip, conservative capping at a tick boundary, and the spot-price / price
helpers at hand-checkable points.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from l2arb.amm import concentrated_liquidity as cl
from l2arb.constants import MAX_SQRT_RATIO, MIN_SQRT_RATIO, Q96

pytestmark = pytest.mark.unit

# A pool centred at price 1 (sqrtP = 2**96) with deep liquidity.
UNITY = Q96
L = 10**18

sqrt_prices = st.integers(min_value=Q96 // 100, max_value=Q96 * 100)
liquidities = st.integers(min_value=10**12, max_value=10**24)
fees = st.integers(min_value=0, max_value=100_000)


# ------------------------------ validation -------------------------------- #
@pytest.mark.parametrize(
    ("sqrtp", "liq", "fee", "amt"),
    [
        (MIN_SQRT_RATIO - 1, L, 3000, 1),
        (MAX_SQRT_RATIO + 1, L, 3000, 1),
        (UNITY, 0, 3000, 1),
        (UNITY, -1, 3000, 1),
        (UNITY, L, -1, 1),
        (UNITY, L, 1_000_000, 1),
        (UNITY, L, 3000, -1),
    ],
)
def test_swap_rejects_bad_inputs(sqrtp: int, liq: int, fee: int, amt: int) -> None:
    with pytest.raises(ValueError):  # noqa: PT011 - guard message varies
        cl.swap_0_for_1(sqrtp, liq, amt, fee)


# --------------------------- price / marginal ----------------------------- #
def test_price0_in_1_at_reference_points() -> None:
    assert cl.price0_in_1(UNITY) == pytest.approx(1.0)
    assert cl.price0_in_1(2 * UNITY) == pytest.approx(4.0)  # (2)^2
    assert cl.price0_in_1(UNITY // 2) == pytest.approx(0.25)


def test_marginal_rates_are_fee_inclusive_and_reciprocal() -> None:
    assert cl.marginal_rate_0_for_1(UNITY, 0) == pytest.approx(1.0)
    assert cl.marginal_rate_0_for_1(UNITY, 3000) == pytest.approx(0.997)
    # At price 1 both directions are symmetric.
    assert cl.marginal_rate_1_for_0(UNITY, 3000) == pytest.approx(0.997)
    # Reciprocity ignoring fee: r01 * r10 == (1-fee)^2.
    r01 = cl.marginal_rate_0_for_1(3 * UNITY, 0)
    r10 = cl.marginal_rate_1_for_0(3 * UNITY, 0)
    assert r01 * r10 == pytest.approx(1.0)


# ------------------------------- direction -------------------------------- #
def test_zero_for_one_lowers_price_and_outputs_token1() -> None:
    step = cl.swap_0_for_1(UNITY, L, 10**15, fee_pips=0)
    assert step.amount_out > 0
    assert step.sqrt_price_next_x96 < UNITY  # price falls
    assert not step.capped
    # At price ~1 with slippage, you receive slightly less token1 than token0 in.
    assert step.amount_out < 10**15
    assert step.amount_out > 10**15 * 99 // 100


def test_one_for_zero_raises_price_and_outputs_token0() -> None:
    step = cl.swap_1_for_0(UNITY, L, 10**15, fee_pips=0)
    assert step.amount_out > 0
    assert step.sqrt_price_next_x96 > UNITY  # price rises


def test_symmetry_at_price_one() -> None:
    a = cl.swap_0_for_1(UNITY, L, 10**15, fee_pips=500).amount_out
    b = cl.swap_1_for_0(UNITY, L, 10**15, fee_pips=500).amount_out
    assert a == b  # perfect symmetry at price 1 with equal liquidity


def test_fee_reduces_output() -> None:
    no_fee = cl.amount_out_0_for_1(UNITY, L, 10**16, 0)
    with_fee = cl.amount_out_0_for_1(UNITY, L, 10**16, 3000)
    assert with_fee < no_fee


def test_zero_input_is_a_noop() -> None:
    step = cl.swap_0_for_1(UNITY, L, 0, 3000)
    assert step == cl.SwapStep(0, 0, UNITY, capped=False)


# -------------------------- conservative capping -------------------------- #
def test_capping_at_a_tick_boundary_understates_output() -> None:
    # A limit just below the current price bounds the move; capped output is a
    # lower bound on the uncapped output.
    tight_limit = UNITY * 999 // 1000
    capped = cl.swap_0_for_1(UNITY, L, 10**18, fee_pips=0, sqrt_price_limit_x96=tight_limit)
    uncapped = cl.swap_0_for_1(UNITY, L, 10**18, fee_pips=0)
    assert capped.capped is True
    assert capped.sqrt_price_next_x96 == tight_limit
    assert capped.amount_out <= uncapped.amount_out
    assert capped.amount_in_consumed <= 10**18


def test_limit_on_wrong_side_swaps_nothing() -> None:
    # For 0->1, a limit at/above the price leaves no room to move.
    step = cl.swap_0_for_1(UNITY, L, 10**15, fee_pips=0, sqrt_price_limit_x96=UNITY)
    assert step == cl.SwapStep(0, 0, UNITY, capped=True)
    # For 1->0, a limit at/below the price leaves no room to move.
    step2 = cl.swap_1_for_0(UNITY, L, 10**15, fee_pips=0, sqrt_price_limit_x96=UNITY)
    assert step2 == cl.SwapStep(0, 0, UNITY, capped=True)


def test_one_for_zero_capping_understates_output() -> None:
    tight = UNITY * 1001 // 1000
    capped = cl.swap_1_for_0(UNITY, L, 10**18, fee_pips=0, sqrt_price_limit_x96=tight)
    uncapped = cl.swap_1_for_0(UNITY, L, 10**18, fee_pips=0)
    assert capped.capped is True
    assert capped.sqrt_price_next_x96 == tight
    assert capped.amount_out <= uncapped.amount_out
    assert capped.amount_in_consumed <= 10**18


def test_capping_with_fee_grosses_up_consumed_input() -> None:
    # A capped step with a non-zero fee exercises the fee-grossing of the
    # consumed input; it must never exceed the supplied amount.
    tight = UNITY * 999 // 1000
    step = cl.swap_0_for_1(UNITY, L, 10**18, fee_pips=3000, sqrt_price_limit_x96=tight)
    assert step.capped is True
    assert 0 < step.amount_in_consumed <= 10**18
    assert step.amount_out > 0


def test_dust_input_moves_no_price_and_yields_nothing() -> None:
    # amount_in=1 at 0.30 % floors the post-fee remainder to 0: no price move,
    # no output (exercises the helper's amount0==0 guard). Both directions.
    fwd = cl.swap_0_for_1(UNITY, L, 1, fee_pips=3000)
    assert fwd == cl.SwapStep(1, 0, UNITY, capped=False)
    back = cl.swap_1_for_0(UNITY, L, 1, fee_pips=3000)
    assert back == cl.SwapStep(1, 0, UNITY, capped=False)


# ------------------------------- properties ------------------------------- #
@settings(max_examples=200, deadline=None)
@given(sp=sqrt_prices, liq=liquidities, fee=fees, data=st.data())
def test_output_monotonic_in_size(sp: int, liq: int, fee: int, data: st.DataObject) -> None:
    dx1 = data.draw(st.integers(min_value=1, max_value=liq))
    dx2 = data.draw(st.integers(min_value=1, max_value=liq))
    lo, hi = sorted((dx1, dx2))
    assert cl.amount_out_0_for_1(sp, liq, lo, fee) <= cl.amount_out_0_for_1(sp, liq, hi, fee)


@settings(max_examples=200, deadline=None)
@given(sp=sqrt_prices, liq=liquidities, data=st.data())
def test_round_trip_never_profits(sp: int, liq: int, data: st.DataObject) -> None:
    # Swap 0->1 then 1->0 through the resulting price: cannot end with more token0.
    dx = data.draw(st.integers(min_value=10**6, max_value=liq // 10))
    fwd = cl.swap_0_for_1(sp, liq, dx, fee_pips=500)
    if fwd.amount_out == 0:
        return
    back = cl.swap_1_for_0(fwd.sqrt_price_next_x96, liq, fwd.amount_out, fee_pips=500)
    assert back.amount_out <= dx


@settings(max_examples=200, deadline=None)
@given(sp=sqrt_prices, liq=liquidities, fee=fees, data=st.data())
def test_spot_dominates_executed(sp: int, liq: int, fee: int, data: st.DataObject) -> None:
    dx = data.draw(st.integers(min_value=1, max_value=liq // 10))
    out = cl.amount_out_0_for_1(sp, liq, dx, fee)
    spot = cl.marginal_rate_0_for_1(sp, fee)
    assert out / dx <= spot * (1 + 1e-6)
