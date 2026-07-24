"""Unit + property tests for Curve StableSwap 2-coin math."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from l2arb.amm import stableswap as ss

pytestmark = pytest.mark.unit

# Balanced stable pool: 1,000,000 units of each 18-dp coin.
BAL = 1_000_000 * 10**18


def test_low_slippage_near_balance() -> None:
    # A stable pool trades near 1:1 for modest size (that's the whole point).
    out = ss.amount_out(BAL, BAL, amp=200, amount_in=10_000 * 10**18, fee_pips=1000)
    inp = 10_000 * 10**18
    # Output within ~0.5% of input (fee 0.1% + tiny slippage).
    assert 0.99 * inp < out < inp


def test_higher_amp_means_less_slippage() -> None:
    inp = 100_000 * 10**18
    low_a = ss.amount_out(BAL, BAL, amp=10, amount_in=inp, fee_pips=0)
    high_a = ss.amount_out(BAL, BAL, amp=5000, amount_in=inp, fee_pips=0)
    assert high_a > low_a  # flatter curve -> more output for the same input


def test_zero_input_is_zero() -> None:
    assert ss.amount_out(BAL, BAL, amp=200, amount_in=0, fee_pips=1000) == 0


def test_fee_reduces_output() -> None:
    inp = 10_000 * 10**18
    no_fee = ss.amount_out(BAL, BAL, 200, inp, 0)
    with_fee = ss.amount_out(BAL, BAL, 200, inp, 4000)
    assert with_fee < no_fee


def test_get_d_of_balanced_pool() -> None:
    # For a perfectly balanced 2-coin pool, D == sum of balances.
    assert ss.get_d((BAL, BAL), amp=200) == 2 * BAL
    # An empty pool has invariant 0.
    assert ss.get_d((0, 0), amp=200) == 0


@pytest.mark.parametrize(
    ("bi", "bo", "amp", "amt", "fee"),
    [
        (0, BAL, 200, 1, 1000),
        (BAL, 0, 200, 1, 1000),
        (BAL, BAL, 0, 1, 1000),
        (BAL, BAL, 200, -1, 1000),
        (BAL, BAL, 200, 1, 1_000_000),
    ],
)
def test_rejects_bad_inputs(bi: int, bo: int, amp: int, amt: int, fee: int) -> None:
    with pytest.raises(ValueError):  # noqa: PT011 - message varies by guard
        ss.amount_out(bi, bo, amp, amt, fee)


def test_marginal_rate_near_one_for_balanced_pool() -> None:
    rate = ss.marginal_rate(BAL, BAL, amp=200, fee_pips=1000)
    assert 0.99 < rate <= 1.0  # ~ (1 - fee), minimal slippage


reserves = st.integers(min_value=10**18, max_value=10**28)


@settings(max_examples=150, deadline=None)
@given(bi=reserves, bo=reserves, amp=st.integers(1, 5000), data=st.data())
def test_output_monotonic_and_no_free_lunch(
    bi: int, bo: int, amp: int, data: st.DataObject
) -> None:
    dx1 = data.draw(st.integers(min_value=1, max_value=bi))
    dx2 = data.draw(st.integers(min_value=1, max_value=bi))
    lo, hi = sorted((dx1, dx2))
    out_lo = ss.amount_out(bi, bo, amp, lo, 1000)
    out_hi = ss.amount_out(bi, bo, amp, hi, 1000)
    assert out_lo <= out_hi  # monotonic in input
    assert out_hi <= bo  # can never output more than the pool holds
