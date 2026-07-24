"""Unit tests for per-graph detection, incl. the incremental-equivalence law."""

from __future__ import annotations

import pytest

from graphkit import GraphKit
from l2arb.engine.detection import detect_on_graph
from l2arb.model.opportunity import Opportunity, StrategyKind

pytestmark = pytest.mark.unit


def _key(o: Opportunity) -> tuple[StrategyKind, frozenset[str], tuple[int, str], int]:
    return (o.strategy, frozenset(o.pool_addresses), o.numeraire.key, o.input_amount)


def test_detects_a_two_hop(gk: type[GraphKit]) -> None:
    a, b = gk.token(1), gk.token(2)
    graph = gk.graph(
        [
            gk.v2(10, a, b, 1000 * 10**18, 1000 * 10**18),
            gk.v2(11, a, b, 1000 * 10**18, 1100 * 10**18),
        ]
    )
    opps = detect_on_graph(graph, hubs=frozenset(), max_hops=4, ctx=gk.profit_ctx())
    assert opps
    assert all(o.strategy is StrategyKind.TWO_HOP for o in opps)


def test_detects_a_triangle(gk: type[GraphKit]) -> None:
    a, b, c = gk.token(1), gk.token(2), gk.token(3)
    graph = gk.graph(
        [
            gk.v2(10, a, b, 1000 * 10**18, 1000 * 10**18),
            gk.v2(11, b, c, 1000 * 10**18, 1000 * 10**18),
            gk.v2(12, c, a, 1000 * 10**18, 1100 * 10**18),
        ]
    )
    opps = detect_on_graph(graph, hubs={a.key}, max_hops=4, ctx=gk.profit_ctx())
    assert any(o.strategy is StrategyKind.TRIANGULAR for o in opps)


def test_incremental_equals_full_sweep(gk: type[GraphKit]) -> None:
    # T-0410: seeding from all tokens must match a full (sources=None) sweep.
    a, b, c = gk.token(1), gk.token(2), gk.token(3)
    graph = gk.graph(
        [
            gk.v2(10, a, b, 1000 * 10**18, 1000 * 10**18),
            gk.v2(11, a, b, 1000 * 10**18, 1100 * 10**18),
            gk.v2(12, b, c, 1000 * 10**18, 1000 * 10**18),
            gk.v2(13, c, a, 1000 * 10**18, 1050 * 10**18),
        ]
    )
    ctx = gk.profit_ctx()
    hubs = {a.key, b.key, c.key}
    full = detect_on_graph(graph, hubs=hubs, max_hops=4, ctx=ctx, sources=None)
    incr = detect_on_graph(graph, hubs=hubs, max_hops=4, ctx=ctx, sources=graph.tokens())
    assert {_key(o) for o in full} == {_key(o) for o in incr}
    assert full  # there is something to compare


def test_candidates_failing_the_gate_are_dropped(gk: type[GraphKit]) -> None:
    # A genuine margin-positive 2-hop candidate exists (9% edge), but a punishing
    # min_bps makes the net gate reject it — exercising the None-skip in the loop.
    a, b = gk.token(1), gk.token(2)
    graph = gk.graph(
        [
            gk.v2(10, a, b, 1000 * 10**18, 1000 * 10**18),
            gk.v2(11, a, b, 1000 * 10**18, 1100 * 10**18),
        ]
    )
    ctx = gk.profit_ctx(min_bps=10_000_000.0)  # absurd threshold rejects the net
    assert detect_on_graph(graph, hubs=frozenset(), max_hops=4, ctx=ctx) == []


def test_max_hops_below_four_skips_multi_hop(gk: type[GraphKit]) -> None:
    # A pure 4-cycle: only multi-hop could find it, so max_hops=3 finds nothing.
    a, b, c, d = gk.token(1), gk.token(2), gk.token(3), gk.token(4)
    graph = gk.graph(
        [
            gk.v2(10, a, b, 1000 * 10**18, 1000 * 10**18),
            gk.v2(11, b, c, 1000 * 10**18, 1000 * 10**18),
            gk.v2(12, c, d, 1000 * 10**18, 1000 * 10**18),
            gk.v2(13, d, a, 1000 * 10**18, 1300 * 10**18),
        ]
    )
    assert detect_on_graph(graph, hubs=frozenset(), max_hops=3, ctx=gk.profit_ctx()) == []
    assert detect_on_graph(graph, hubs=frozenset(), max_hops=4, ctx=gk.profit_ctx())
