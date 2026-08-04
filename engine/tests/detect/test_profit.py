"""Unit + property tests for the net-profit gate.

Includes T-0409: an opportunity is reported **only** when it is genuinely
net-profitable, and a reported opportunity's numbers are internally consistent
(never a net loss dressed up as a win).
"""

from __future__ import annotations

from itertools import pairwise

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from graphkit import GraphKit
from l2arb.detect.profit import GasModel, MevModel, ProfitContext, evaluate
from l2arb.detect.two_hop import two_hop_candidates
from l2arb.graph.rategraph import RateEdge, RateGraph
from l2arb.model.opportunity import StrategyKind
from l2arb.model.token import TokenKey

pytestmark = pytest.mark.unit


def _ctx(gas_price_wei: int = 10**6, min_bps: float = 1.0) -> ProfitContext:
    # Numeraire treated as the 18-dp native gas token: 1 wei == 1 base unit.
    gas = GasModel(
        gas_price_wei=gas_price_wei, base_gas=100_000, per_hop_gas=80_000, safety_multiplier=1.5
    )
    return ProfitContext(gas_cost_fn=gas.cost_fn(lambda _key: 1.0), min_profit_bps=min_bps)


def _edge(graph: RateGraph, src: TokenKey, dst: TokenKey, pool: str) -> RateEdge:
    return next(e for e in graph.edges_between(src, dst) if e.pool == pool)


def _profitable_two_pool(gk: type[GraphKit]) -> tuple[RateGraph, list[RateEdge]]:
    a, b = gk.token(1), gk.token(2)
    p1 = gk.v2(10, a, b, 1000 * 10**18, 1000 * 10**18)
    p2 = gk.v2(11, a, b, 1000 * 10**18, 1100 * 10**18)
    graph = gk.graph([p1, p2])
    cycle = [_edge(graph, a.key, b.key, p2.address), _edge(graph, b.key, a.key, p1.address)]
    return graph, cycle


# --------------------------------- basics --------------------------------- #
def test_reports_a_profitable_two_hop(gk: type[GraphKit]) -> None:
    graph, _ = _profitable_two_pool(gk)
    opps = [
        o
        for c in two_hop_candidates(graph)
        if (o := evaluate(c, graph, StrategyKind.TWO_HOP, _ctx())) is not None
    ]
    assert opps
    opp = opps[0]
    assert opp.net_profit > 0
    assert opp.hops == 2
    # Internally consistent accounting.
    assert opp.output_amount - opp.input_amount == opp.gross_profit
    assert opp.net_profit == opp.gross_profit - opp.gas_cost
    assert opp.profit_bps == pytest.approx(opp.net_profit / opp.input_amount * 10_000.0)
    # Risk + score populated.
    assert 0.0 < opp.risk.success_probability <= 0.99
    assert 0.0 < opp.risk.capture_ratio <= 1.0
    assert opp.score > 0
    assert opp.expected_net <= opp.net_profit  # risk-adjusted <= raw


def test_legs_chain_correctly(gk: type[GraphKit]) -> None:
    graph, cycle = _profitable_two_pool(gk)
    opp = evaluate(cycle, graph, StrategyKind.TWO_HOP, _ctx())
    assert opp is not None
    # Each leg's output feeds the next leg's input; last returns to the numeraire.
    for prev, nxt in pairwise(opp.legs):
        assert prev.amount_out == nxt.amount_in
    assert opp.legs[0].amount_in == opp.input_amount
    assert opp.legs[-1].amount_out == opp.output_amount
    assert opp.legs[-1].token_out.key == opp.numeraire.key


def test_arbitrage_free_cycle_is_rejected(gk: type[GraphKit]) -> None:
    a, b = gk.token(1), gk.token(2)
    p1 = gk.v2(10, a, b, 1000 * 10**18, 1000 * 10**18)
    p2 = gk.v2(11, a, b, 1000 * 10**18, 1000 * 10**18)
    graph = gk.graph([p1, p2])
    cycle = [_edge(graph, a.key, b.key, p1.address), _edge(graph, b.key, a.key, p2.address)]
    assert evaluate(cycle, graph, StrategyKind.TWO_HOP, _ctx()) is None


def test_gas_can_make_it_unprofitable(gk: type[GraphKit]) -> None:
    graph, cycle = _profitable_two_pool(gk)
    # A ruinous gas price wipes out the edge.
    assert evaluate(cycle, graph, StrategyKind.TWO_HOP, _ctx(gas_price_wei=10**24)) is None


def test_min_profit_bps_gate(gk: type[GraphKit]) -> None:
    graph, cycle = _profitable_two_pool(gk)
    # A wildly high threshold rejects an otherwise-profitable opportunity.
    assert evaluate(cycle, graph, StrategyKind.TWO_HOP, _ctx(min_bps=100_000.0)) is None


def test_empty_cycle_returns_none(gk: type[GraphKit]) -> None:
    assert evaluate([], RateGraph(GraphKit.CHAIN), StrategyKind.TWO_HOP, _ctx()) is None


def test_verified_flag_reflects_pools(gk: type[GraphKit]) -> None:
    graph, cycle = _profitable_two_pool(gk)
    opp = evaluate(cycle, graph, StrategyKind.TWO_HOP, _ctx())
    assert opp is not None
    assert opp.verified is True  # GraphKit pools default to verified


def test_any_unverified_pool_in_the_cycle_is_rejected(gk: type[GraphKit]) -> None:
    # CLAUDE.md §3: only real, verifiable on-chain data may produce an
    # opportunity. One unverified leg must veto the whole cycle, even though the
    # cycle is otherwise identical to the profitable one above.
    a, b = gk.token(1), gk.token(2)
    p1 = gk.v2(10, a, b, 1000 * 10**18, 1000 * 10**18, verified=False)
    p2 = gk.v2(11, a, b, 1000 * 10**18, 1100 * 10**18)
    graph = gk.graph([p1, p2])
    cycle = [_edge(graph, a.key, b.key, p2.address), _edge(graph, b.key, a.key, p1.address)]
    assert evaluate(cycle, graph, StrategyKind.TWO_HOP, _ctx()) is None


def test_freshness_gate_is_opt_in(gk: type[GraphKit]) -> None:
    # Without now_ts/max_pool_age_seconds set on the context, evaluate() never
    # reads the wall clock and never rejects on staleness — the default context
    # (as used by every other test in this file) is unaffected.
    graph, cycle = _profitable_two_pool(gk)
    ctx = _ctx()
    assert ctx.now_ts is None
    assert ctx.max_pool_age_seconds is None
    assert evaluate(cycle, graph, StrategyKind.TWO_HOP, ctx) is not None


def test_stale_pool_is_rejected_when_freshness_is_enforced(gk: type[GraphKit]) -> None:
    from dataclasses import replace

    graph, cycle = _profitable_two_pool(gk)
    pool_ts = GraphKit.BS.timestamp
    fresh_ctx = replace(_ctx(), now_ts=pool_ts + 60, max_pool_age_seconds=120)
    assert (
        evaluate(cycle, graph, StrategyKind.TWO_HOP, fresh_ctx) is not None
    )  # 60s old, within 120s
    stale_ctx = replace(_ctx(), now_ts=pool_ts + 121, max_pool_age_seconds=120)
    assert evaluate(cycle, graph, StrategyKind.TWO_HOP, stale_ctx) is None  # 121s old, past 120s


def test_cycle_through_stableswap_pool(gk: type[GraphKit]) -> None:
    # A stable pool (~1:1) and a skewed V2 pool price the pair differently -> arb.
    a, b = gk.token(1, decimals=18), gk.token(2, decimals=18)
    stable = gk.stable(10, a, b, 10**24, 10**24, amp=500)
    v2 = gk.v2(11, a, b, 1000 * 10**18, 1200 * 10**18)
    graph = gk.graph([stable, v2])
    opps = [
        o
        for c in two_hop_candidates(graph)
        if (o := evaluate(c, graph, StrategyKind.TWO_HOP, _ctx())) is not None
    ]
    assert opps
    assert all(o.net_profit > 0 for o in opps)
    assert any(stable.address in o.pool_addresses for o in opps)  # routes through the stable pool


def test_cycle_through_weighted_pool(gk: type[GraphKit]) -> None:
    a, b = gk.token(1, decimals=18), gk.token(2, decimals=18)
    weighted = gk.weighted(10, a, b, 10**24, 10**24, weight0=8 * 10**17, weight1=2 * 10**17)
    v2 = gk.v2(11, a, b, 10**24, 10**24)
    graph = gk.graph([weighted, v2])
    # Whichever direction is margin-positive; evaluate the weighted-first leg.
    opps = [
        o
        for c in two_hop_candidates(graph)
        if (o := evaluate(c, graph, StrategyKind.TWO_HOP, _ctx())) is not None
    ]
    assert any(weighted.address == o.legs[0].pool_address for o in opps)


def test_cross_dex_cycle_with_v3_first_hop(gk: type[GraphKit]) -> None:
    # A cross-dex 2-hop whose first hop is a V3 pool (exercises V3 size seeding).
    a, b = gk.token(1, decimals=18), gk.token(2, decimals=18)
    v3 = gk.v3(10, a, b, int((1.1**0.5) * gk.UNITY_SQRT), 10**22)  # ~1.1 B per A
    v2 = gk.v2(11, a, b, 1000 * 10**18, 1000 * 10**18)  # ~1 B per A
    graph = gk.graph([v3, v2])
    # A -> B on the richer (V3) pool, B -> A on the V2 pool.
    cycle = [_edge(graph, a.key, b.key, v3.address), _edge(graph, b.key, a.key, v2.address)]
    opp = evaluate(cycle, graph, StrategyKind.TWO_HOP, _ctx())
    assert opp is not None
    assert opp.net_profit > 0
    assert opp.legs[0].pool_address == v3.address
    # The V3 leg carries no active-range boundary, so its size rode an unbounded
    # single-tick estimate: the opportunity is flagged, never silently trusted.
    assert "v3_single_tick_estimate" in opp.risk.notes


def test_bounded_v3_leg_is_not_flagged(gk: type[GraphKit]) -> None:
    # The same cross-dex cycle, but the V3 pool carries its active-range lower
    # boundary — the fill is a safe bound, so the estimate note is absent.
    a, b = gk.token(1, decimals=18), gk.token(2, decimals=18)
    v3 = gk.v3(
        10,
        a,
        b,
        int((1.1**0.5) * gk.UNITY_SQRT),
        10**22,
        sqrt_ratio_lower_x96=gk.UNITY_SQRT,  # 1.0*Q96, below the current ~1.05*Q96 price
    )
    v2 = gk.v2(11, a, b, 1000 * 10**18, 1000 * 10**18)
    graph = gk.graph([v3, v2])
    cycle = [_edge(graph, a.key, b.key, v3.address), _edge(graph, b.key, a.key, v2.address)]
    opp = evaluate(cycle, graph, StrategyKind.TWO_HOP, _ctx())
    assert opp is not None
    assert opp.net_profit > 0
    assert "v3_single_tick_estimate" not in opp.risk.notes


# ------------------------------- risk model ------------------------------- #
def test_more_hops_lower_success_probability() -> None:
    mev = MevModel()
    two = mev.assess(hops=2, profit_bps=20.0, is_cross_chain=False)
    four = mev.assess(hops=4, profit_bps=20.0, is_cross_chain=False)
    assert four.success_probability < two.success_probability


def test_cross_chain_penalised_and_big_edges_more_competed() -> None:
    mev = MevModel()
    same = mev.assess(hops=2, profit_bps=20.0, is_cross_chain=False)
    cross = mev.assess(hops=2, profit_bps=20.0, is_cross_chain=True)
    assert cross.success_probability < same.success_probability
    small_edge = mev.assess(hops=2, profit_bps=5.0, is_cross_chain=False)
    big_edge = mev.assess(hops=2, profit_bps=500.0, is_cross_chain=False)
    assert big_edge.capture_ratio < small_edge.capture_ratio  # obvious edges competed away


# --------------------------- E4: settle-time risk -------------------------- #
# The old cross_chain_success_penalty was a flat constant, blind to
# settle_seconds even though it's already computed and carried on Opportunity —
# a 30s fast-bridge opp and a 60-minute canonical-bridge opp got an identical
# confidence haircut. These pin the settle-time-scaled replacement.
def test_cross_chain_penalty_scales_with_settle_seconds() -> None:
    mev = MevModel()
    fast = mev.assess(hops=2, profit_bps=20.0, is_cross_chain=True, settle_seconds=30)
    slow = mev.assess(hops=2, profit_bps=20.0, is_cross_chain=True, settle_seconds=3600)
    assert fast.success_probability > slow.success_probability
    # And the fast bridge is now measurably closer to (though still below) the
    # same-chain baseline than the flat old constant would have allowed.
    same_chain = mev.assess(hops=2, profit_bps=20.0, is_cross_chain=False)
    assert same_chain.success_probability - fast.success_probability < 0.1


def test_cross_chain_penalty_settle_seconds_defaults_to_zero() -> None:
    # Backward-compat call sites (same-chain evaluate() always passes 0 too, via
    # the parameter default) must be unaffected: omitting settle_seconds is
    # identical to passing 0 explicitly.
    mev = MevModel()
    omitted = mev.assess(hops=2, profit_bps=20.0, is_cross_chain=True)
    explicit_zero = mev.assess(hops=2, profit_bps=20.0, is_cross_chain=True, settle_seconds=0)
    assert omitted.success_probability == explicit_zero.success_probability
    assert omitted.notes == explicit_zero.notes


def test_cross_chain_penalty_formula_is_pinned() -> None:
    mev = MevModel()
    r = mev.assess(hops=2, profit_bps=20.0, is_cross_chain=True, settle_seconds=600)
    # 600s = 10 minutes; well within the [0.05, 0.99] clamp, so no floor/ceiling
    # interference — this pins the raw formula, not just its clamped shape.
    expected_penalty = mev.cross_chain_base_penalty + mev.cross_chain_penalty_per_minute * 10.0
    expected_success = mev.base_success_probability - expected_penalty
    assert 0.05 < expected_success < 0.99  # sanity: this case doesn't hit the clamp
    assert r.success_probability == pytest.approx(expected_success)
    assert any(n.startswith("price_drift_risk_penalty=") for n in r.notes)
    assert "settle_seconds=600" in r.notes


def test_cross_chain_penalty_note_absent_for_same_chain() -> None:
    # The price-drift risk note (docs/ARBITRAGE_THEORY.md §5) is a cross-chain-only
    # concept; a same-chain assessment must not carry a meaningless drift note.
    mev = MevModel()
    r = mev.assess(hops=2, profit_bps=20.0, is_cross_chain=False)
    assert not any(n.startswith("price_drift_risk_penalty=") for n in r.notes)
    assert not any(n.startswith("settle_seconds=") for n in r.notes)


def test_cross_chain_penalty_floor_stays_sane_for_a_very_long_wait() -> None:
    # A pathologically long settle time must clamp to the existing sane floor
    # (0.05), never go negative or otherwise nonsensical.
    mev = MevModel()
    r = mev.assess(hops=2, profit_bps=20.0, is_cross_chain=True, settle_seconds=24 * 3600)
    assert 0.05 <= r.success_probability <= 0.99


# ----------------------------- property (T-0409) -------------------------- #
@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.filter_too_much])
@given(
    r0a=st.integers(min_value=10**18, max_value=10**24),
    r1a=st.integers(min_value=10**18, max_value=10**24),
    r0b=st.integers(min_value=10**18, max_value=10**24),
    r1b=st.integers(min_value=10**18, max_value=10**24),
)
def test_never_reports_a_net_loss(r0a: int, r1a: int, r0b: int, r1b: int) -> None:
    gk = GraphKit
    a, b = gk.token(1), gk.token(2)
    graph = gk.graph([gk.v2(10, a, b, r0a, r1a), gk.v2(11, a, b, r0b, r1b)])
    ctx = _ctx()
    for c in two_hop_candidates(graph):
        opp = evaluate(c, graph, StrategyKind.TWO_HOP, ctx)
        if opp is None:
            continue
        # If reported, it MUST be genuinely net-profitable and self-consistent.
        assert opp.net_profit > 0
        assert opp.output_amount - opp.input_amount == opp.gross_profit
        assert opp.net_profit == opp.gross_profit - opp.gas_cost
        assert opp.net_profit / opp.input_amount * 10_000.0 >= ctx.min_profit_bps - 1e-6
