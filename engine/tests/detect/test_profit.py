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
    assert opp.verified is False  # test pools are unverified by default


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
