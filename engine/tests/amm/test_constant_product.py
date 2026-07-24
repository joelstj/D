"""Unit + property tests for exact constant-product (V2) swap math.

The concrete cases pin the arithmetic against hand-computed values that also
match Uniswap V2's ``getAmountOut``/``getAmountIn`` bit-for-bit. The property
tests pin the *laws* the math must obey for any valid pool (T-0302):
constant-product invariant, monotonicity, sufficiency of ``amount_in_for_out``,
and no-free-lunch (spot rate dominates the executed rate).
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from l2arb.amm import constant_product as cp

pytestmark = pytest.mark.unit

# Bounded but realistically large (V2 reserves reach ~2**112).
reserves = st.integers(min_value=1_000, max_value=10**30)
fees = st.integers(min_value=0, max_value=100_000)  # up to 10 %


# --------------------------- concrete / hand-computed --------------------- #
def test_get_amount_out_matches_uniswap_reference() -> None:
    # Canonical example: (in=1000, out=1000) reserves, 100 in, 0.30 % fee -> 90.
    assert cp.amount_out_for_in(1000, 1000, 100, 3000) == 90


def test_get_amount_in_round_trips_the_reference() -> None:
    # To receive 90 out of the same pool you must supply exactly 100 in.
    assert cp.amount_in_for_out(1000, 1000, 90, 3000) == 100


def test_zero_fee_beats_positive_fee_on_a_large_pool() -> None:
    r = 10**24
    dx = 10**21
    assert cp.amount_out_for_in(r, r, dx, 0) > cp.amount_out_for_in(r, r, dx, 3000)


def test_zero_input_and_zero_output_are_zero() -> None:
    assert cp.amount_out_for_in(1000, 1000, 0, 3000) == 0
    assert cp.amount_in_for_out(1000, 1000, 0, 3000) == 0


def test_swap_exact_in_returns_post_trade_reserves() -> None:
    out, new_in, new_out = cp.swap_exact_in(1000, 1000, 100, 3000)
    assert out == 90
    assert new_in == 1100
    assert new_out == 910


@pytest.mark.parametrize(
    ("ri", "ro", "amt", "fee"),
    [
        (0, 1000, 1, 3000),
        (1000, 0, 1, 3000),
        (-1, 1000, 1, 3000),
        (1000, 1000, -1, 3000),
        (1000, 1000, 1, -1),
        (1000, 1000, 1, 1_000_000),
    ],
)
def test_amount_out_rejects_bad_inputs(ri: int, ro: int, amt: int, fee: int) -> None:
    with pytest.raises(ValueError):  # noqa: PT011 - message varies by which guard trips
        cp.amount_out_for_in(ri, ro, amt, fee)


def test_amount_in_rejects_undrainable_output() -> None:
    with pytest.raises(ValueError, match="undrainable"):
        cp.amount_in_for_out(1000, 1000, 1000, 3000)
    with pytest.raises(ValueError, match="undrainable"):
        cp.amount_in_for_out(1000, 1000, 1001, 3000)


def test_amount_in_rejects_negative_output() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        cp.amount_in_for_out(1000, 1000, -1, 3000)


def test_marginal_rate_symmetry_of_a_balanced_pool() -> None:
    # Equal reserves, 0.30 % fee -> marginal rate is exactly (1 - fee).
    assert cp.marginal_rate(1000, 1000, 3000) == pytest.approx(0.997)


def test_price_impact_zero_for_zero_and_grows_with_size() -> None:
    assert cp.price_impact_bps(10**24, 10**24, 0, 3000) == 0.0
    small = cp.price_impact_bps(10**24, 10**24, 10**20, 3000)
    big = cp.price_impact_bps(10**24, 10**24, 10**23, 3000)
    assert 0.0 < small < big < 10_000.0


# ------------------------------- properties ------------------------------- #
@settings(max_examples=250, deadline=None)
@given(ri=reserves, ro=reserves, fee=fees, data=st.data())
def test_constant_product_invariant_weakly_increases(
    ri: int, ro: int, fee: int, data: st.DataObject
) -> None:
    amount_in = data.draw(st.integers(min_value=0, max_value=ri * 1000))
    _, new_in, new_out = cp.swap_exact_in(ri, ro, amount_in, fee)
    assert new_in * new_out >= ri * ro


@settings(max_examples=250, deadline=None)
@given(ri=reserves, ro=reserves, fee=fees, data=st.data())
def test_amount_out_is_monotonic_in_input(ri: int, ro: int, fee: int, data: st.DataObject) -> None:
    dx1 = data.draw(st.integers(min_value=1, max_value=ri * 100))
    dx2 = data.draw(st.integers(min_value=1, max_value=ri * 100))
    lo, hi = sorted((dx1, dx2))
    assert cp.amount_out_for_in(ri, ro, lo, fee) <= cp.amount_out_for_in(ri, ro, hi, fee)


@settings(max_examples=250, deadline=None)
@given(ri=reserves, ro=reserves, fee=fees, data=st.data())
def test_amount_in_for_out_is_sufficient(ri: int, ro: int, fee: int, data: st.DataObject) -> None:
    # Round the pool up first so a valid, drainable target output exists.
    dy = data.draw(st.integers(min_value=1, max_value=ro - 1))
    dx = cp.amount_in_for_out(ri, ro, dy, fee)
    # Supplying the computed input yields at least the requested output.
    assert cp.amount_out_for_in(ri, ro, dx, fee) >= dy


@settings(max_examples=250, deadline=None)
@given(ri=reserves, ro=reserves, fee=fees, data=st.data())
def test_spot_rate_dominates_executed_rate(ri: int, ro: int, fee: int, data: st.DataObject) -> None:
    dx = data.draw(st.integers(min_value=1, max_value=ri * 100))
    out = cp.amount_out_for_in(ri, ro, dx, fee)
    spot = cp.marginal_rate(ri, ro, fee)
    # You never execute at better than the (fee-inclusive) marginal rate.
    assert out / dx <= spot + 1e-9
