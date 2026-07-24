"""Bounded multi-hop detection — negative cycles up to ``max_hops`` long.

Two-stage, composing the graph primitives (docs/ARBITRAGE_THEORY §3.3-3.4):

1. :func:`l2arb.graph.tropical.negative_cycle_roots` sweeps the graph and flags
   every token that sits on a negative cycle of length ``<= max_hops`` — a cheap,
   dense pre-filter.
2. From each flagged root, a **bounded DFS** over the best-edge adjacency recovers
   a concrete simple cycle (its actual pools), which the profit gate then prices.

Because every token on a simple negative cycle is flagged in stage 1, recovering
from all roots and de-duplicating yields every bounded simple negative cycle;
roots that only witness a non-simple walk simply recover nothing. Passing
``sources`` keeps only cycles that touch a changed token (incremental re-search).
"""

from __future__ import annotations

from collections.abc import Iterable

from l2arb.detect.cycle import Cycle
from l2arb.graph.rategraph import RateEdge, RateGraph
from l2arb.graph.tropical import negative_cycle_roots
from l2arb.model.token import TokenKey

__all__ = ["multi_hop_candidates"]


def _canonical_pools(pools: list[str]) -> tuple[str, ...]:
    rotations = [tuple(pools[i:] + pools[:i]) for i in range(len(pools))]
    return min(rotations)


def _recover_cycle(
    adjacency: dict[TokenKey, list[RateEdge]],
    root: TokenKey,
    min_hops: int,
    max_hops: int,
    min_log_margin: float,
) -> Cycle | None:
    """Bounded DFS for one simple negative cycle rooted at ``root`` (first found)."""
    path: list[RateEdge] = []
    visited: set[TokenKey] = {root}
    found: Cycle | None = None

    def dfs(node: TokenKey, acc: float) -> None:
        nonlocal found
        depth = len(path)
        for edge in adjacency.get(node, ()):
            nxt = edge.dst
            weight = acc + edge.log_weight
            if nxt == root:
                if depth + 1 >= min_hops and weight < -min_log_margin:
                    found = [*path, edge]
                    return
                continue
            if nxt in visited or depth + 1 >= max_hops:
                continue
            visited.add(nxt)
            path.append(edge)
            dfs(nxt, weight)
            path.pop()
            visited.discard(nxt)
            if found is not None:
                return

    dfs(root, 0.0)
    return found


def multi_hop_candidates(
    graph: RateGraph,
    max_hops: int,
    min_hops: int = 2,
    sources: Iterable[TokenKey] | None = None,
    min_log_margin: float = 0.0,
) -> list[Cycle]:
    """Return distinct bounded negative cycles (length ``min_hops..max_hops``)."""
    if min_hops < 2:
        raise ValueError(f"min_hops must be >= 2, got {min_hops}")
    best = graph.best_edges()
    adjacency = {src: list(row.values()) for src, row in best.items()}
    source_set = None if sources is None else set(sources)

    seen: set[tuple[str, ...]] = set()
    out: list[Cycle] = []
    for root in negative_cycle_roots(graph, max_hops):
        cycle = _recover_cycle(adjacency, root, min_hops, max_hops, min_log_margin)
        if cycle is None:
            continue
        if source_set is not None and not source_set.intersection(e.src for e in cycle):
            continue
        key = _canonical_pools([e.pool for e in cycle])
        if key not in seen:
            seen.add(key)
            out.append(cycle)
    return out
