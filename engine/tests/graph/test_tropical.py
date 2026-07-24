"""Unit tests for the tropical (min-plus) K-hop sweep."""

from __future__ import annotations

import numpy as np
import pytest

from graphkit import GraphKit
from l2arb.graph import tropical
from l2arb.graph.rategraph import RateGraph
from l2arb.graph.tropical import bounded_min_closed_walk, negative_cycle_roots, warmup

pytestmark = pytest.mark.unit


def test_min_plus_matmul_numpy_hand_checked() -> None:
    inf = np.inf
    x = np.array([[0.0, 2.0], [inf, 0.0]])
    y = np.array([[0.0, 3.0], [1.0, 0.0]])
    out = tropical._min_plus_matmul_numpy(x, y)
    assert np.array_equal(out, np.array([[0.0, 2.0], [1.0, 0.0]]))


def test_numba_matches_numpy_reference() -> None:
    # The JIT kernel must agree with the numpy oracle bit-for-bit (deterministic input).
    n = 12
    x = (np.arange(n * n, dtype=np.float64) % 7 - 3).reshape(n, n)
    y = (np.arange(n * n, dtype=np.float64) % 5 - 2).reshape(n, n)
    reference = tropical._min_plus_matmul_numpy(x, y)
    selected = tropical._min_plus_matmul(x, y)  # numba when available
    assert np.array_equal(selected, reference)


def test_warmup_is_safe_to_call() -> None:
    warmup()
    warmup()  # idempotent


def test_empty_graph() -> None:
    g = RateGraph(42161)
    assert bounded_min_closed_walk(g, max_hops=4) == {}
    assert negative_cycle_roots(g, max_hops=4) == set()


def test_max_hops_validation(gk: type[GraphKit]) -> None:
    a, b = gk.token(1), gk.token(2)
    g = gk.graph([gk.v2(10, a, b, 10**18, 10**18)])
    with pytest.raises(ValueError, match="max_hops must be >= 2"):
        bounded_min_closed_walk(g, max_hops=1)


def test_flags_all_nodes_of_a_negative_triangle(gk: type[GraphKit]) -> None:
    a, b, c = gk.token(1), gk.token(2), gk.token(3)
    ab = gk.v2(10, a, b, 1000 * 10**18, 1000 * 10**18)
    bc = gk.v2(11, b, c, 1000 * 10**18, 1000 * 10**18)
    ca = gk.v2(12, c, a, 1000 * 10**18, 1100 * 10**18)
    graph = gk.graph([ab, bc, ca])
    roots = negative_cycle_roots(graph, max_hops=3)
    # Every token on the simple negative cycle must be flagged.
    assert {a.key, b.key, c.key} <= roots
    walk = bounded_min_closed_walk(graph, max_hops=3)
    assert all(walk[t] < 0 for t in (a.key, b.key, c.key))


def test_hop_bound_excludes_longer_cycles(gk: type[GraphKit]) -> None:
    # A pure 4-cycle: no 2- or 3-hop negative cycle exists.
    a, b, c, d = gk.token(1), gk.token(2), gk.token(3), gk.token(4)
    pools = [
        gk.v2(10, a, b, 1000 * 10**18, 1000 * 10**18),
        gk.v2(11, b, c, 1000 * 10**18, 1000 * 10**18),
        gk.v2(12, c, d, 1000 * 10**18, 1000 * 10**18),
        gk.v2(13, d, a, 1000 * 10**18, 1300 * 10**18),
    ]
    graph = gk.graph(pools)
    assert negative_cycle_roots(graph, max_hops=3) == set()  # too short to see it
    assert negative_cycle_roots(graph, max_hops=4)  # now visible


def test_no_roots_when_arbitrage_free(gk: type[GraphKit]) -> None:
    a, b, c = gk.token(1), gk.token(2), gk.token(3)
    ab = gk.v2(10, a, b, 1000 * 10**18, 1000 * 10**18)
    bc = gk.v2(11, b, c, 1000 * 10**18, 1000 * 10**18)
    ca = gk.v2(12, c, a, 1000 * 10**18, 1000 * 10**18)
    assert negative_cycle_roots(gk.graph([ab, bc, ca]), max_hops=5) == set()
