"""Unit + property tests for Balancer weighted 2-token math."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from l2arb.amm import weighted as wp

pytestmark = pytest.mark.unit

UNIT = 10**18
BAL = 1_000_000 * 10**18


def test_5050_matches_constant_product_shape() -> None:
    # 50/50 weighted pool with equal balances -> ~constant product behaviour.
    out = wp.amount_out(BAL, BAL, UNIT // 2, UNIT // 2, 100_000 * 10**18, fee_pips=0)
    # x*y=k gives out = y*dx/(x+dx) = BAL*0.1BAL/1.1BAL ~= 0.0909*BAL.
    assert 90_000 * 10**18 < out < 91_000 * 10**18


def test_output_never_exceeds_balance() -> None:
    out = wp.amount_out(BAL, BAL, UNIT // 2, UNIT // 2, 10**40, fee_pips=0)
    assert out < BAL  # asymptotes to the out-balance, never reaches it


def test_extreme_weight_ratio_never_drains_the_pool() -> None:
    # Regression (found by the adversarial stress suite): a very skewed weight ratio
    # plus a huge input makes `power` underflow the Decimal precision to 0, which
    # would round the output up to the full balance — an impossible drain that would
    # overstate profit. It must be clamped to balance_out - 1.
    out = wp.amount_out(10**6, 10**6, weight_in=29, weight_out=1, amount_in=119_045_494, fee_pips=0)
    assert out == 10**6 - 1
    # Even at the most extreme skew and input, output stays strictly below balance.
    drained = wp.amount_out(
        1000, 1000, weight_in=10**18, weight_out=1, amount_in=10**30, fee_pips=0
    )
    assert drained == 999


def test_weight_skew_changes_marginal_rate() -> None:
    # 80/20 pool: the 20%-weight token is 4x "denser" -> marginal 0->1 is ~4.
    rate = wp.marginal_rate(BAL, BAL, weight_in=8 * 10**17, weight_out=2 * 10**17, fee_pips=0)
    assert rate == pytest.approx(4.0)


def test_fee_reduces_output_and_zero_input() -> None:
    inp = 10_000 * 10**18
    assert wp.amount_out(BAL, BAL, UNIT // 2, UNIT // 2, 0, 3000) == 0
    no_fee = wp.amount_out(BAL, BAL, UNIT // 2, UNIT // 2, inp, 0)
    with_fee = wp.amount_out(BAL, BAL, UNIT // 2, UNIT // 2, inp, 3000)
    assert with_fee < no_fee


def test_dust_input_flooring() -> None:
    # A tiny input whose post-fee remainder floors to 0 yields 0.
    assert wp.amount_out(BAL, BAL, UNIT // 2, UNIT // 2, 1, fee_pips=999_999) == 0


@pytest.mark.parametrize(
    ("bi", "bo", "wi", "wo", "amt", "fee"),
    [
        (0, BAL, UNIT, UNIT, 1, 0),
        (BAL, 0, UNIT, UNIT, 1, 0),
        (BAL, BAL, 0, UNIT, 1, 0),
        (BAL, BAL, UNIT, 0, 1, 0),
        (BAL, BAL, UNIT, UNIT, -1, 0),
        (BAL, BAL, UNIT, UNIT, 1, 1_000_000),
    ],
)
def test_rejects_bad_inputs(bi: int, bo: int, wi: int, wo: int, amt: int, fee: int) -> None:
    with pytest.raises(ValueError):  # noqa: PT011 - message varies by guard
        wp.amount_out(bi, bo, wi, wo, amt, fee)


bals = st.integers(min_value=10**18, max_value=10**28)
weights = st.integers(min_value=10**16, max_value=10**18)


@settings(max_examples=150, deadline=None)
@given(bi=bals, bo=bals, wi=weights, wo=weights, data=st.data())
def test_output_monotonic_and_bounded(
    bi: int, bo: int, wi: int, wo: int, data: st.DataObject
) -> None:
    dx1 = data.draw(st.integers(min_value=1, max_value=bi))
    dx2 = data.draw(st.integers(min_value=1, max_value=bi))
    lo, hi = sorted((dx1, dx2))
    out_lo = wp.amount_out(bi, bo, wi, wo, lo, 3000)
    out_hi = wp.amount_out(bi, bo, wi, wo, hi, 3000)
    assert out_lo <= out_hi
    assert out_hi < bo  # floored, never reaches the out-balance
