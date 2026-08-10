"""De-duplicate and rank opportunities into the top-N most valuable.

Different detectors (or parallel pools) can surface the *same* arbitrage — same
pools, same numeraire. De-duplication keeps the single best-scored instance, then
opportunities are ordered by their **risk-adjusted score** (expected value after
MEV competition and success probability), and the top ``n`` are returned. This is
the "top 10 most profitable opportunities" the brief asks for; ``n`` is caller-set.
"""

from __future__ import annotations

from collections.abc import Iterable

from l2arb.model.opportunity import Opportunity

__all__ = ["rank_opportunities"]

# Two opportunities are "the same" if they trade the same pool set in the same
# numeraire — regardless of which detector or hop ordering surfaced them. Each
# pool is qualified by the chain it lives on (not just its bare address string):
# a cross-chain opportunity's two legs live on two different chains, and several
# of this engine's shipped chains are OP-Stack siblings that legitimately share
# identical predeploy addresses (root CLAUDE.md §12 finding I2 hit the same shape
# of bug in ingestion's own pool-verification set). Without the chain tag, a
# genuine cross-chain opportunity whose remote leg's address happens to coincide
# with an unrelated same-chain opportunity's pool would collide in this key and
# one would be silently dropped as a "duplicate" of the other.
_DedupKey = tuple[frozenset[tuple[int, str]], int, str]


def _dedup_key(opp: Opportunity) -> _DedupKey:
    chain, address = opp.numeraire.key
    pools = frozenset((leg.token_in.chain_id, leg.pool_address) for leg in opp.legs)
    return (pools, chain, address)


def rank_opportunities(opportunities: Iterable[Opportunity], top_n: int) -> list[Opportunity]:
    """Return the ``top_n`` opportunities by score, de-duplicated by pool set.

    ``top_n <= 0`` returns an empty list. Ties are broken by raw ``net_profit`` so
    the ordering is deterministic.
    """
    if top_n <= 0:
        return []
    best: dict[_DedupKey, Opportunity] = {}
    for opp in opportunities:
        key = _dedup_key(opp)
        incumbent = best.get(key)
        if incumbent is None or opp.score > incumbent.score:
            best[key] = opp
    ranked = sorted(best.values(), key=lambda o: (o.score, o.net_profit), reverse=True)
    return ranked[:top_n]
