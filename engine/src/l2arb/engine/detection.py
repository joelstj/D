"""Run every single-chain detector over one graph and price the survivors.

Composes the three detectors (2-hop, triangular, bounded multi-hop) with the
net-profit gate into a single pass. The detectors partition by length so their
outputs barely overlap: 2-hop covers length 2, triangular length 3, and multi-hop
lengths ``4..max_hops`` (``min_hops=4``). Passing ``sources`` restricts every
detector to cycles touching a changed token — the incremental per-block path,
which is provably equivalent to a full sweep when ``sources`` covers all tokens
(pinned by a test, T-0410).
"""

from __future__ import annotations

from collections.abc import Iterable

from l2arb.detect.cycle import Cycle
from l2arb.detect.multi_hop import multi_hop_candidates
from l2arb.detect.profit import ProfitContext, evaluate
from l2arb.detect.triangular import triangular_candidates
from l2arb.detect.two_hop import two_hop_candidates
from l2arb.graph.rategraph import RateGraph
from l2arb.model.opportunity import Opportunity, StrategyKind
from l2arb.model.token import TokenKey

__all__ = ["detect_on_graph"]


def detect_on_graph(
    graph: RateGraph,
    *,
    hubs: Iterable[TokenKey],
    max_hops: int,
    ctx: ProfitContext,
    sources: Iterable[TokenKey] | None = None,
) -> list[Opportunity]:
    """Detect and net-price every profitable cycle on ``graph``.

    ``sources`` (default: all tokens) seeds the incremental path. Returns the
    surviving :class:`Opportunity` objects (unranked, not yet de-duplicated).
    """
    source_set = None if sources is None else set(sources)
    opportunities: list[Opportunity] = []

    def _price(cycles: list[Cycle], strategy: StrategyKind) -> None:
        for cycle in cycles:
            opp = evaluate(cycle, graph, strategy, ctx)
            if opp is not None:
                opportunities.append(opp)

    _price(two_hop_candidates(graph, sources=source_set), StrategyKind.TWO_HOP)
    _price(triangular_candidates(graph, hubs, sources=source_set), StrategyKind.TRIANGULAR)
    if max_hops >= 4:
        _price(
            multi_hop_candidates(graph, max_hops, min_hops=4, sources=source_set),
            StrategyKind.MULTI_HOP,
        )
    return opportunities
