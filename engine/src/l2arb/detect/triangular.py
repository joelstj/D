"""Triangular (3-hop) detection — a hub-rooted 3-cycle ``H -> X -> Y -> H``.

Most L2 liquidity concentrates around a few **hub** tokens (WETH, USDC, USDT,
DAI). Rooting the 3-cycle search at hubs keeps the branching factor small while
still covering the cycles that actually carry size (docs/ARBITRAGE_THEORY §3.2).
Each leg may use any pool / DEX family, so this subsumes **cross-dex 3-hop**.

Candidates are de-duplicated by the rotation-invariant ordered pool tuple, so the
same directed triangle discovered from different hub roots is emitted once. As
elsewhere, a candidate is a marginal signal; the profit gate re-prices it exactly.
"""

from __future__ import annotations

from collections.abc import Iterable

from l2arb.detect.cycle import Cycle
from l2arb.graph.rategraph import RateGraph
from l2arb.model.token import TokenKey

__all__ = ["triangular_candidates"]


def _canonical_pools(pools: list[str]) -> tuple[str, ...]:
    """Rotation-invariant key preserving direction: min rotation of the pool tuple."""
    rotations = [tuple(pools[i:] + pools[:i]) for i in range(len(pools))]
    return min(rotations)


def triangular_candidates(
    graph: RateGraph,
    hubs: Iterable[TokenKey],
    sources: Iterable[TokenKey] | None = None,
    min_log_margin: float = 0.0,
) -> list[Cycle]:
    """Return distinct margin-profitable triangles rooted at ``hubs``.

    Only ``hubs`` present in the graph are used as roots. When ``sources`` is
    given, a triangle is kept only if it touches a changed token (incremental
    re-search). ``min_log_margin`` sets the marginal threshold as in
    :func:`l2arb.detect.two_hop.two_hop_candidates`.
    """
    hub_set = set(hubs)
    source_set = None if sources is None else set(sources)
    seen: set[tuple[str, ...]] = set()
    out: list[Cycle] = []
    for h in hub_set:
        for e_hx in graph.out_edges(h):
            x = e_hx.dst  # x != h: pools never self-loop
            for e_xy in graph.out_edges(x):
                y = e_xy.dst
                if y in (h, x):
                    continue
                for e_yh in graph.edges_between(y, h):
                    # h, x, y are distinct and each leg uses a different pool (a
                    # pool serves one token pair), so the cycle is always simple.
                    total = e_hx.log_weight + e_xy.log_weight + e_yh.log_weight
                    if total >= -min_log_margin:
                        continue
                    if source_set is not None and not source_set.intersection({h, x, y}):
                        continue
                    key = _canonical_pools([e_hx.pool, e_xy.pool, e_yh.pool])
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append([e_hx, e_xy, e_yh])
    return out
