"""Unit tests for the bounded multi-hop detector."""

from __future__ import annotations

import pytest

from graphkit import GraphKit
from l2arb.detect.cycle import cycle_log_margin, is_closed, is_simple
from l2arb.detect.multi_hop import multi_hop_candidates
from l2arb.graph.rategraph import RateGraph
from l2arb.model.token import Token

pytestmark = pytest.mark.unit


def _four_cycle(gk: type[GraphKit]) -> tuple[tuple[Token, Token, Token, Token], RateGraph]:
    a, b, c, d = gk.token(1), gk.token(2), gk.token(3), gk.token(4)
    pools = [
        gk.v2(10, a, b, 1000 * 10**18, 1000 * 10**18),
        gk.v2(11, b, c, 1000 * 10**18, 1000 * 10**18),
        gk.v2(12, c, d, 1000 * 10**18, 1000 * 10**18),
        gk.v2(13, d, a, 1000 * 10**18, 1300 * 10**18),  # 30% edge
    ]
    return (a, b, c, d), gk.graph(pools)


def test_finds_bounded_four_cycle(gk: type[GraphKit]) -> None:
    _, graph = _four_cycle(gk)
    cands = multi_hop_candidates(graph, max_hops=4)
    assert cands
    for cyc in cands:
        assert is_closed(cyc)
        assert is_simple(cyc)
        assert cycle_log_margin(cyc) < 0


def test_hop_bound_hides_the_four_cycle(gk: type[GraphKit]) -> None:
    _, graph = _four_cycle(gk)
    assert multi_hop_candidates(graph, max_hops=3) == []


def test_min_hops_validation(gk: type[GraphKit]) -> None:
    _, graph = _four_cycle(gk)
    with pytest.raises(ValueError, match="min_hops must be >= 2"):
        multi_hop_candidates(graph, max_hops=4, min_hops=1)


def test_dedup_single_cycle(gk: type[GraphKit]) -> None:
    _, graph = _four_cycle(gk)
    # Each root on the cycle would recover it; output must be de-duplicated.
    assert len(multi_hop_candidates(graph, max_hops=4)) == 1


def test_sources_filter(gk: type[GraphKit]) -> None:
    (a, *_), graph = _four_cycle(gk)
    ghost = gk.token(99)
    assert multi_hop_candidates(graph, max_hops=4, sources={ghost.key}) == []
    assert multi_hop_candidates(graph, max_hops=4, sources={a.key})


def test_no_candidates_when_arbitrage_free(gk: type[GraphKit]) -> None:
    a, b, c = gk.token(1), gk.token(2), gk.token(3)
    pools = [
        gk.v2(10, a, b, 1000 * 10**18, 1000 * 10**18),
        gk.v2(11, b, c, 1000 * 10**18, 1000 * 10**18),
        gk.v2(12, c, a, 1000 * 10**18, 1000 * 10**18),
    ]
    assert multi_hop_candidates(gk.graph(pools), max_hops=5) == []


def test_min_hops_excludes_shorter_cycles(gk: type[GraphKit]) -> None:
    # A 2-hop arb exists; requiring min_hops=3 should skip it.
    a, b = gk.token(1), gk.token(2)
    p1 = gk.v2(10, a, b, 1000 * 10**18, 1000 * 10**18)
    p2 = gk.v2(11, a, b, 1000 * 10**18, 1100 * 10**18)
    graph = gk.graph([p1, p2])
    assert multi_hop_candidates(graph, max_hops=4, min_hops=3) == []
    assert multi_hop_candidates(graph, max_hops=4, min_hops=2)  # length-2 allowed
