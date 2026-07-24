"""Unit tests for the hub-rooted triangular (3-hop) detector."""

from __future__ import annotations

import pytest

from graphkit import GraphKit
from l2arb.detect.cycle import cycle_log_margin, cycle_tokens, is_closed, is_simple
from l2arb.detect.triangular import triangular_candidates
from l2arb.graph.rategraph import RateGraph
from l2arb.model.token import Token

pytestmark = pytest.mark.unit


def _planted_triangle(gk: type[GraphKit]) -> tuple[Token, Token, Token, RateGraph]:
    # A->B->C->A with a ~10% edge on the C->A leg (all 18dp for clean rates).
    a, b, c = gk.token(1), gk.token(2), gk.token(3)
    ab = gk.v2(10, a, b, 1000 * 10**18, 1000 * 10**18)  # 1 B per A
    bc = gk.v2(11, b, c, 1000 * 10**18, 1000 * 10**18)  # 1 C per B
    ca = gk.v2(12, c, a, 1000 * 10**18, 1100 * 10**18)  # 1.1 A per C
    return a, b, c, gk.graph([ab, bc, ca])


def test_finds_planted_triangle(gk: type[GraphKit]) -> None:
    a, b, c, graph = _planted_triangle(gk)
    cands = triangular_candidates(graph, hubs={a.key})
    assert cands
    for cyc in cands:
        assert is_closed(cyc)
        assert is_simple(cyc)
        assert len(cyc) == 3
        assert cycle_log_margin(cyc) < 0
        assert set(cycle_tokens(cyc)) == {a.key, b.key, c.key}


def test_dedup_across_hub_roots(gk: type[GraphKit]) -> None:
    a, b, c, graph = _planted_triangle(gk)
    # Rooting at every vertex must not emit the same directed triangle 3x.
    cands = triangular_candidates(graph, hubs={a.key, b.key, c.key})
    assert len(cands) == 1


def test_no_false_positive_when_arbitrage_free(gk: type[GraphKit]) -> None:
    a, b, c = gk.token(1), gk.token(2), gk.token(3)
    ab = gk.v2(10, a, b, 1000 * 10**18, 1000 * 10**18)
    bc = gk.v2(11, b, c, 1000 * 10**18, 1000 * 10**18)
    ca = gk.v2(12, c, a, 1000 * 10**18, 1000 * 10**18)  # perfectly balanced
    assert triangular_candidates(gk.graph([ab, bc, ca]), hubs={a.key, b.key, c.key}) == []


def test_sources_filter_keeps_only_touching_cycles(gk: type[GraphKit]) -> None:
    a, b, _c, graph = _planted_triangle(gk)
    d = gk.token(9)
    # A source set disjoint from the triangle filters it out.
    assert triangular_candidates(graph, hubs={a.key}, sources={d.key}) == []
    # A source on the triangle keeps it.
    assert triangular_candidates(graph, hubs={a.key}, sources={b.key})


def test_hub_not_in_graph_is_skipped(gk: type[GraphKit]) -> None:
    *_, graph = _planted_triangle(gk)
    ghost = gk.token(42)
    assert triangular_candidates(graph, hubs={ghost.key}) == []
