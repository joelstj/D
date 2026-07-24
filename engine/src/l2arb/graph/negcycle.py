"""Negative-cycle detection over the ``-ln(rate)`` graph (Bellman-Ford).

A negative-weight cycle is exactly a margin-profitable arbitrage loop
(docs/ARBITRAGE_THEORY §2). This module finds one with Bellman-Ford relaxation
and recovers the actual edge cycle via predecessor pointers — the general
detector behind bounded multi-hop search (T-0407).

We initialise every distance to 0 (equivalent to a virtual source with a 0-weight
edge to every node) so the search finds *any* negative cycle in the graph, not
just those reachable from one root. The multigraph is collapsed to its best
(min-weight) edge per ordered pair for the search; the recovered cycle references
those concrete pools for exact re-pricing.

A small ``EPS`` guards the relaxation test so float noise in ``-ln`` cannot
manufacture a phantom cycle — the exact profit gate is the final arbiter anyway,
so we err toward not looping forever rather than toward maximal sensitivity.
"""

from __future__ import annotations

from typing import cast

from l2arb.detect.cycle import Cycle
from l2arb.graph.rategraph import RateEdge, RateGraph
from l2arb.model.token import TokenKey

__all__ = ["find_negative_cycle"]

EPS = 1e-12


def _all_nodes(best: dict[TokenKey, dict[TokenKey, RateEdge]]) -> list[TokenKey]:
    nodes: set[TokenKey] = set(best.keys())
    for row in best.values():
        nodes.update(row.keys())
    return list(nodes)


def find_negative_cycle(graph: RateGraph) -> Cycle | None:
    """Return one negative-weight cycle as an edge list, or ``None`` if none exists.

    ``O(V*E)`` worst case. The returned cycle is closed and its edges chain
    head-to-tail; it is a *candidate* (marginal) — re-price it exactly before
    reporting.
    """
    best = graph.best_edges()
    edges = [e for row in best.values() for e in row.values()]
    if not edges:
        return None
    nodes = _all_nodes(best)
    dist: dict[TokenKey, float] = dict.fromkeys(nodes, 0.0)
    pred: dict[TokenKey, RateEdge | None] = dict.fromkeys(nodes, None)

    relaxed_node: TokenKey | None = None
    for _ in range(len(nodes)):
        relaxed_node = None
        for e in edges:
            if dist[e.src] + e.log_weight < dist[e.dst] - EPS:
                dist[e.dst] = dist[e.src] + e.log_weight
                pred[e.dst] = e
                relaxed_node = e.dst
        if relaxed_node is None:
            return None  # settled with no negative cycle

    # A node still relaxing after |V| rounds sits on or downstream of a negative
    # cycle. Step back |V| predecessors to land *inside* the cycle, then extract it.
    # The casts encode algorithm invariants: after a full |V| rounds the last
    # relaxed node is set, and every node on a negative cycle has a predecessor.
    cursor = cast(TokenKey, relaxed_node)
    for _ in range(len(nodes)):
        cursor = cast(RateEdge, pred[cursor]).src

    return _extract_cycle(cursor, pred)


def _extract_cycle(start: TokenKey, pred: dict[TokenKey, RateEdge | None]) -> Cycle:
    """Walk predecessors from a node known to be on the cycle and return its edges."""
    cycle: Cycle = []
    node = start
    while True:
        edge = cast(RateEdge, pred[node])
        cycle.append(edge)
        node = edge.src
        if node == start:
            break
    cycle.reverse()
    return cycle
