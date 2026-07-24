"""Unit tests for Bellman-Ford negative-cycle detection + recovery."""

from __future__ import annotations

import pytest

from graphkit import GraphKit
from l2arb.detect.cycle import cycle_log_margin, is_closed
from l2arb.graph.negcycle import find_negative_cycle
from l2arb.graph.rategraph import RateGraph

pytestmark = pytest.mark.unit


def test_empty_graph_has_no_cycle() -> None:
    assert find_negative_cycle(RateGraph(42161)) is None


def test_finds_a_negative_triangle(gk: type[GraphKit]) -> None:
    a, b, c = gk.token(1), gk.token(2), gk.token(3)
    ab = gk.v2(10, a, b, 1000 * 10**18, 1000 * 10**18)
    bc = gk.v2(11, b, c, 1000 * 10**18, 1000 * 10**18)
    ca = gk.v2(12, c, a, 1000 * 10**18, 1100 * 10**18)  # 10% edge
    cycle = find_negative_cycle(gk.graph([ab, bc, ca]))
    assert cycle is not None
    assert is_closed(cycle)
    assert cycle_log_margin(cycle) < 0


def test_no_cycle_when_arbitrage_free(gk: type[GraphKit]) -> None:
    a, b, c = gk.token(1), gk.token(2), gk.token(3)
    ab = gk.v2(10, a, b, 1000 * 10**18, 1000 * 10**18)
    bc = gk.v2(11, b, c, 1000 * 10**18, 1000 * 10**18)
    ca = gk.v2(12, c, a, 1000 * 10**18, 1000 * 10**18)
    assert find_negative_cycle(gk.graph([ab, bc, ca])) is None


def test_acyclic_graph_returns_none(gk: type[GraphKit]) -> None:
    # A single pool gives A<->B edges (a 2-cycle) but it is a fee loss, not
    # negative; recovery must return None rather than a spurious cycle.
    a, b = gk.token(1), gk.token(2)
    assert find_negative_cycle(gk.graph([gk.v2(10, a, b, 10**18, 10**18)])) is None


def test_finds_longer_negative_cycle(gk: type[GraphKit]) -> None:
    # A 4-cycle A->B->C->D->A carrying a net edge.
    a, b, c, d = gk.token(1), gk.token(2), gk.token(3), gk.token(4)
    pools = [
        gk.v2(10, a, b, 1000 * 10**18, 1000 * 10**18),
        gk.v2(11, b, c, 1000 * 10**18, 1000 * 10**18),
        gk.v2(12, c, d, 1000 * 10**18, 1000 * 10**18),
        gk.v2(13, d, a, 1000 * 10**18, 1200 * 10**18),  # 20% edge closes the loop
    ]
    cycle = find_negative_cycle(gk.graph(pools))
    assert cycle is not None
    assert cycle_log_margin(cycle) < 0
