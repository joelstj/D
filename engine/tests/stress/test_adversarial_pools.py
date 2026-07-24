"""Adversarial / stress robustness for the AMM math and the detection engine.

Real ingestion feeds are hostile: a bot can hand the engine a pool at the edge of
``uint112``, a single-wei dust pool, a 99.99 %-fee trap, a V3 pool pinned at the
protocol price bounds, or a StableSwap with a pathological amplification. None of
these may crash the engine, overflow, or — the cardinal sin — cause it to report a
cycle that is not genuinely net-profitable (CLAUDE.md §5 "fail loud on bad data";
docs/ARBITRAGE_THEORY §1.2 "understate, never fabricate").

These tests hammer the exact math with property-based extremes and drive the whole
engine over a graph deliberately salted with degenerate pools, asserting the
economic invariants hold on every reported opportunity.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from graphkit import GraphKit
from l2arb.amm import concentrated_liquidity as cl
from l2arb.amm import constant_product as cp
from l2arb.amm import quote
from l2arb.amm import stableswap as ss
from l2arb.amm import weighted as wp
from l2arb.constants import MAX_SQRT_RATIO, MIN_SQRT_RATIO
from l2arb.engine.engine import ArbitrageEngine
from l2arb.errors import PoolStateError
from l2arb.model.opportunity import Opportunity

pytestmark = pytest.mark.unit

MAX_UINT112 = 2**112 - 1
MAX_UINT128 = 2**128 - 1


# --------------------------------------------------------------------------- #
# Exact-math robustness under extreme state (property-based)                    #
# --------------------------------------------------------------------------- #
@settings(max_examples=300, deadline=None)
@given(
    reserve_in=st.integers(1, MAX_UINT112),
    reserve_out=st.integers(1, MAX_UINT112),
    amount_in=st.integers(0, 2**130),
    fee_pips=st.integers(0, 999_999),
)
def test_v2_output_is_always_bounded(
    reserve_in: int, reserve_out: int, amount_in: int, fee_pips: int
) -> None:
    # No matter how extreme the reserves/fee, output is a non-negative integer that
    # never drains the pool (0 <= out < reserve_out) — no overflow, no free tokens.
    out = cp.amount_out_for_in(reserve_in, reserve_out, amount_in, fee_pips)
    assert isinstance(out, int)
    assert 0 <= out < reserve_out


@settings(max_examples=200, deadline=None)
@given(
    reserve_in=st.integers(1, MAX_UINT112),
    reserve_out=st.integers(1, MAX_UINT112),
    a=st.integers(0, 2**120),
    b=st.integers(0, 2**120),
    fee_pips=st.integers(0, 999_999),
)
def test_v2_output_is_monotonic_in_input(
    reserve_in: int, reserve_out: int, a: int, b: int, fee_pips: int
) -> None:
    # More in never buys less out — a bigger trade cannot get a better total fill.
    lo, hi = sorted((a, b))
    assert cp.amount_out_for_in(reserve_in, reserve_out, lo, fee_pips) <= cp.amount_out_for_in(
        reserve_in, reserve_out, hi, fee_pips
    )


def test_v2_at_the_uint112_ceiling_does_not_overflow() -> None:
    # Both reserves at the on-chain maximum, a whale-sized swap: pure-Python ints
    # mean no wraparound, and the invariant still holds.
    out = cp.amount_out_for_in(MAX_UINT112, MAX_UINT112, 10**30, 3000)
    assert 0 < out < MAX_UINT112


def test_v2_single_wei_pool_is_priceable() -> None:
    # A dust pool must not crash; it simply cannot give more than it holds.
    assert cp.amount_out_for_in(1, 1, 10**18, 3000) == 0
    assert cp.amount_out_for_in(10**18, 1, 10**18, 3000) == 0


def test_v2_near_total_fee_floors_output_to_zero() -> None:
    # A 99.99 %-fee trap pool yields ~nothing and never a phantom gain.
    out = cp.amount_out_for_in(10**24, 10**24, 10**18, 999_900)
    assert out >= 0
    assert out < cp.amount_out_for_in(10**24, 10**24, 10**18, 3000)


@settings(max_examples=200, deadline=None)
@given(
    sqrt_price=st.integers(MIN_SQRT_RATIO, MAX_SQRT_RATIO),
    liquidity=st.integers(1, MAX_UINT128),
    amount_in=st.integers(0, 2**100),
    fee_pips=st.integers(0, 999_999),
)
def test_v3_single_tick_is_bounded_both_directions(
    sqrt_price: int, liquidity: int, amount_in: int, fee_pips: int
) -> None:
    # The single-tick step never raises and never returns a negative amount, at any
    # price in the protocol range, in either swap direction.
    out01 = cl.amount_out_0_for_1(sqrt_price, liquidity, amount_in, fee_pips)
    out10 = cl.amount_out_1_for_0(sqrt_price, liquidity, amount_in, fee_pips)
    assert out01 >= 0
    assert out10 >= 0


def test_v3_at_price_extremes_caps_without_crashing() -> None:
    # Pinned at the very bottom/top of the price range: the swap that would push
    # price out of range is capped, so output is 0 in the impossible direction.
    liq = 10**18
    # At MIN price, 1->0 has room to rise but 0->1 cannot fall further.
    assert cl.amount_out_0_for_1(MIN_SQRT_RATIO, liq, 10**18, 500) == 0
    # At MAX price, 0->1 has room to fall but 1->0 cannot rise further.
    assert cl.amount_out_1_for_0(MAX_SQRT_RATIO, liq, 10**18, 500) == 0


@settings(max_examples=150, deadline=None)
@given(
    balance_in=st.integers(10**6, 10**30),
    balance_out=st.integers(10**6, 10**30),
    amp=st.integers(1, 10**6),
    amount_in=st.integers(0, 10**28),
    fee_pips=st.integers(0, 999_999),
)
def test_stableswap_newton_converges_and_stays_bounded(
    balance_in: int, balance_out: int, amp: int, amount_in: int, fee_pips: int
) -> None:
    # Even with a pathological amplification and a badly imbalanced pool, both Newton
    # iterations terminate and the output never exceeds the output balance.
    out = ss.amount_out(balance_in, balance_out, amp, amount_in, fee_pips)
    assert 0 <= out < balance_out


@settings(max_examples=150, deadline=None)
@given(
    balance_in=st.integers(10**6, 10**30),
    balance_out=st.integers(10**6, 10**30),
    weight_in=st.integers(1, 10**18),
    weight_out=st.integers(1, 10**18),
    amount_in=st.integers(0, 10**28),
    fee_pips=st.integers(0, 999_999),
)
def test_weighted_output_is_floored_and_bounded(
    balance_in: int,
    balance_out: int,
    weight_in: int,
    weight_out: int,
    amount_in: int,
    fee_pips: int,
) -> None:
    # Extreme weight ratios (e.g. 1:1e18) must not overstate output or crash the
    # Decimal power; output is floored and can never drain the pool.
    out = wp.amount_out(balance_in, balance_out, weight_in, weight_out, amount_in, fee_pips)
    assert 0 <= out < balance_out


# --------------------------------------------------------------------------- #
# Whole-engine invariants over a deliberately hostile graph                     #
# --------------------------------------------------------------------------- #
def _assert_opportunity_is_sound(opp: Opportunity, min_bps: float) -> None:
    """Every reported opportunity must be genuinely, provably net-profitable."""
    assert opp.net_profit > 0, "reported a non-profitable opportunity"
    assert opp.profit_bps >= min_bps, "reported below the min-profit threshold"
    assert opp.output_amount > opp.input_amount, "gross output did not exceed input"
    assert opp.gross_profit == opp.output_amount - opp.input_amount
    assert opp.net_profit == opp.gross_profit - opp.gas_cost - opp.bridge_cost
    # Legs chain end-to-end with no gaps.
    assert opp.legs[0].amount_in == opp.input_amount
    assert opp.legs[-1].amount_out == opp.output_amount
    for prev, nxt in zip(opp.legs, opp.legs[1:], strict=False):
        assert prev.amount_out == nxt.amount_in
    # Risk fields are well-formed probabilities.
    assert 0.0 <= opp.risk.success_probability <= 1.0
    assert 0.0 <= opp.risk.capture_ratio <= 1.0
    assert 0.0 <= opp.risk.frontrun_risk <= 1.0


def _hostile_engine(gk: type[GraphKit]) -> tuple[ArbitrageEngine, float]:
    """A graph salted with degenerate pools plus a couple of genuine arbitrages."""
    min_bps = 1.0
    engine = ArbitrageEngine(max_hops=4)
    engine.configure_chain(gk.CHAIN, gk.profit_ctx(min_bps=min_bps))
    t = [gk.token(i + 1) for i in range(8)]

    pools = [
        # Degenerate / adversarial pools that must never yield a phantom edge.
        gk.v2(200, t[0], t[1], MAX_UINT112, MAX_UINT112, fee=3000),
        gk.v2(201, t[1], t[2], 10**24, 10**24, fee=999_900),  # 99.99% fee trap
        gk.v2(202, t[2], t[3], 10**30, 10**6, fee=3000),  # wild imbalance
        gk.v3(203, t[3], t[4], MIN_SQRT_RATIO, 10**18, fee=500),  # price floor
        gk.v3(204, t[4], t[5], MAX_SQRT_RATIO, 10**18, fee=500),  # price ceiling
        gk.stable(205, t[5], t[6], 10**24, 10**24, amp=10**6, fee=1000),  # extreme amp
        # Two genuine 2-hop spatial arbitrages (same pair, mispriced second pool) so
        # the engine has real work to find amid the noise.
        gk.v2(206, t[6], t[7], 1000 * 10**18, 1000 * 10**18, fee=3000),
        gk.v2(207, t[6], t[7], 1000 * 10**18, 1080 * 10**18, fee=3000),
        gk.v2(208, t[0], t[2], 1000 * 10**18, 1000 * 10**18, fee=3000),
        gk.v2(209, t[0], t[2], 1000 * 10**18, 1060 * 10**18, fee=3000),
    ]
    for pool in pools:
        engine.ingest(pool)
    return engine, min_bps


def test_engine_never_reports_a_loss_on_a_hostile_graph(gk: type[GraphKit]) -> None:
    engine, min_bps = _hostile_engine(gk)
    opportunities = engine.compute(top_n=10)
    # It must find the planted real arbs (proving it still works amid the noise)...
    assert opportunities, "expected the genuine planted arbitrages to be detected"
    # ...and every single reported opportunity must be provably sound.
    for opp in opportunities:
        _assert_opportunity_is_sound(opp, min_bps)


def test_non_tradable_pools_produce_no_opportunity(gk: type[GraphKit]) -> None:
    # A graph of only zero-liquidity / one-sided pools yields nothing — degenerate
    # state can never manufacture an edge.
    engine = ArbitrageEngine(max_hops=4)
    engine.configure_chain(gk.CHAIN, gk.profit_ctx(min_bps=1.0))
    a, b, c = gk.token(1), gk.token(2), gk.token(3)
    engine.ingest(gk.v2(300, a, b, 0, 0))  # empty
    engine.ingest(gk.v2(301, b, c, 10**18, 0))  # one-sided
    engine.ingest(gk.v3(302, a, c, GraphKit.UNITY_SQRT, 0))  # no active liquidity
    assert engine.compute(top_n=10) == []


def test_hostile_graph_is_deterministic(gk: type[GraphKit]) -> None:
    # Same inputs -> identical ranked output, run to run (no wall-clock, no RNG).
    e1, _ = _hostile_engine(gk)
    e2, _ = _hostile_engine(gk)
    r1 = [(o.pool_addresses, o.net_profit) for o in e1.compute(top_n=10)]
    r2 = [(o.pool_addresses, o.net_profit) for o in e2.compute(top_n=10)]
    assert r1 == r2


def test_quote_rejects_a_token_not_in_the_pool(gk: type[GraphKit]) -> None:
    # Fail loud, not silently mis-price, when asked for a token the pool doesn't hold.
    pool = gk.v2(310, gk.token(1), gk.token(2), 10**21, 10**21)
    with pytest.raises(PoolStateError):
        quote.amount_out(pool, gk.token(99).key, 10**18)
