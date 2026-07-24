"""Candidate cycles — the currency between detectors and the profit gate.

A :data:`Cycle` is an ordered list of :class:`RateEdge` forming a closed loop:
``edge[i].dst == edge[i+1].src`` and ``edge[-1].dst == edge[0].src``. It is what
the graph-search detectors emit (candidate generation) and what the exact
re-pricing / net-profit gate consumes (ADR-005). Cycles carry only marginal-rate
weights, so a cycle here is a *hypothesis* of profit, not a confirmed one.

Helpers here are pure and cheap: they describe a cycle's tokens, pools, and hop
count, and check the structural invariants a detector must uphold.
"""

from __future__ import annotations

from itertools import pairwise

from l2arb.graph.rategraph import RateEdge
from l2arb.model.token import TokenKey

__all__ = [
    "Cycle",
    "cycle_log_margin",
    "cycle_pools",
    "cycle_tokens",
    "is_closed",
    "is_simple",
]

# A closed sequence of directed rate edges. See module docstring for the invariant.
Cycle = list[RateEdge]


def is_closed(cycle: Cycle) -> bool:
    """True iff the edges chain head-to-tail and the last returns to the first."""
    if not cycle:
        return False
    for prev, nxt in pairwise(cycle):
        if prev.dst != nxt.src:
            return False
    return cycle[-1].dst == cycle[0].src


def cycle_tokens(cycle: Cycle) -> list[TokenKey]:
    """The token nodes visited, starting and ending at the numeraire (which is
    listed once, at the front): ``[t0, t1, ..., t_{k-1}]`` for a k-hop cycle."""
    return [edge.src for edge in cycle]


def cycle_pools(cycle: Cycle) -> list[str]:
    """The pool address used at each hop, in order."""
    return [edge.pool for edge in cycle]


def is_simple(cycle: Cycle) -> bool:
    """True iff no token is visited twice (a simple cycle) and no pool repeats.

    Repeated pools or intermediate tokens usually indicate a degenerate loop that
    the exact re-pricing would reject anyway; keeping cycles simple bounds the
    search and matches how a real flash-loan route is built.
    """
    tokens = cycle_tokens(cycle)
    if len(set(tokens)) != len(tokens):
        return False
    pools = cycle_pools(cycle)
    return len(set(pools)) == len(pools)


def cycle_log_margin(cycle: Cycle) -> float:
    """Sum of edge ``log_weight``s: ``-ln(prod rate)``. Negative ⇒ margin-profitable.

    This is the candidate test — strictly a *marginal* (infinitesimal-size)
    signal. Size-aware exact profit is computed later by the profit gate.
    """
    return sum(edge.log_weight for edge in cycle)
