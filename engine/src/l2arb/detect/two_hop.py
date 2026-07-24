"""Spatial 2-hop detection — the same pair mispriced across two pools.

The simplest and most reliable arbitrage: a token pair ``(A, B)`` quoted
differently on two pools ``P1`` and ``P2``. Buy ``B`` where it is cheap, sell it
where it is dear. This is a length-2 negative cycle ``A -> B -> A`` using distinct
pools (docs/ARBITRAGE_THEORY §3.1), and it naturally spans **different DEXes**
(cross-dex) whenever ``P1`` and ``P2`` belong to different DEX families — the
detector is agnostic; it just sees two pools on the same pair.

It is always-on and cheap: for each token and each neighbour it pairs the
outbound and inbound pools. Passing ``sources`` restricts the scan to changed
(dirty) tokens for incremental per-block re-search.

Candidates are *marginal* signals only; each is re-priced at an optimal size by
the profit gate before anything is reported.
"""

from __future__ import annotations

from collections.abc import Iterable

from l2arb.detect.cycle import Cycle
from l2arb.graph.rategraph import RateGraph
from l2arb.model.token import TokenKey

__all__ = ["two_hop_candidates"]


def two_hop_candidates(
    graph: RateGraph,
    sources: Iterable[TokenKey] | None = None,
    min_log_margin: float = 0.0,
) -> list[Cycle]:
    """Return distinct margin-profitable 2-hop cycles ``A -> B -> A``.

    A candidate is emitted when the two hops use **different pools** and their
    combined ``log_weight`` is below ``-min_log_margin`` (i.e. product of rates
    ``> exp(min_log_margin) >= 1``). ``sources`` (default: all tokens) seeds the
    scan from specific tokens for incremental search. Each ``(start, buy-pool,
    sell-pool)`` triple is enumerated exactly once, so no de-duplication is needed.
    """
    starts = graph.tokens() if sources is None else set(sources)
    out: list[Cycle] = []
    for a in starts:
        for e_ab in graph.out_edges(a):
            b = e_ab.dst
            for e_ba in graph.edges_between(b, a):
                if e_ba.pool == e_ab.pool:
                    continue  # a round trip in one pool is always a loss
                if e_ab.log_weight + e_ba.log_weight < -min_log_margin:
                    out.append([e_ab, e_ba])
    return out
