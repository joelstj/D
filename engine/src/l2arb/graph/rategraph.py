"""The exchange-rate graph — candidate generator for cyclic arbitrage.

Each token on a chain is a node; each pool contributes two directed edges (one
per swap direction) carrying the **executable marginal rate**. The load-bearing
transform (docs/ARBITRAGE_THEORY §2) is the edge weight

    ``w(X -> Y) = -ln(rate)``

so that for any cycle ``C``: ``sum_{e in C} w(e) = -ln(prod rate_e)``. Hence a
**profitable cycle (product of rates > 1) is exactly a negative-weight cycle**.
That single identity powers the 2-hop, triangular, and multi-hop detectors.

Rates are **human-unit** (whole-token out per whole-token in), i.e. the base-unit
rate scaled by ``10**(dec_in - dec_out)``. Units still cancel around a cycle, but
keeping weights near 0 makes the float ``-ln`` sum well-conditioned so the sign
test is robust. The graph is only a *candidate generator*: every cycle it surfaces
is re-priced with the exact integer AMM math at an optimal size before anything is
reported (ADR-005), so a marginally-mis-scored edge can never produce a false
"free money" report — only a missed candidate, which the periodic full sweep
catches.

The graph updates **in place**: one pool update rewrites exactly its two edges in
O(1) and marks its two tokens dirty, so the engine can re-search only the changed
neighbourhood (docs/ARBITRAGE_THEORY §3.4).
"""

from __future__ import annotations

import math
from typing import NamedTuple

from l2arb.amm import quote
from l2arb.model.pool import PoolState
from l2arb.model.token import TokenKey

__all__ = ["RateEdge", "RateGraph"]


class RateEdge(NamedTuple):
    """A directed edge ``src -> dst`` priced by a single pool.

    ``log_weight = -ln(rate)`` (negative when the hop is favourable). ``pool`` is
    the pool address, the key back into :meth:`RateGraph.pool` for exact
    re-pricing. ``rate`` (human units) is retained for the cheap 2-hop margin
    check and for diagnostics.
    """

    src: TokenKey
    dst: TokenKey
    pool: str
    rate: float
    log_weight: float


class RateGraph:
    """A per-chain, in-place exchange-rate multigraph with dirty tracking."""

    def __init__(self, chain_id: int) -> None:
        self.chain_id = chain_id
        self._pools: dict[str, PoolState] = {}
        # adj[src][dst][pool_address] = edge  — nested for O(1) per-pool rewrite.
        self._adj: dict[TokenKey, dict[TokenKey, dict[str, RateEdge]]] = {}
        self._dirty: set[TokenKey] = set()

    # ------------------------------------------------------------------ #
    # Mutation
    # ------------------------------------------------------------------ #
    def upsert_pool(self, pool: PoolState) -> set[TokenKey]:
        """Insert or update ``pool``'s two edges in place; return its two tokens.

        An untradable pool (zero liquidity on a side) **or a pool touching a
        quarantined token** is treated as a removal so the graph never carries an
        unpriceable — or mispriced — edge (see :meth:`_priceable`). The pool is
        still stored in the pool map for provenance; it simply contributes no rate
        edge, so no cycle can route through it. Both endpoint tokens are marked
        dirty for the next incremental search.
        """
        if pool.chain_id != self.chain_id:
            raise ValueError(f"pool chain {pool.chain_id} != graph chain {self.chain_id}")
        self._remove_pool_edges(pool.address)
        self._pools[pool.address] = pool
        if self._priceable(pool):
            t0, t1 = pool.token0.key, pool.token1.key
            self._add_edge(self._make_edge(pool, t0, t1))
            self._add_edge(self._make_edge(pool, t1, t0))
        touched = {pool.token0.key, pool.token1.key}
        self._dirty |= touched
        return touched

    @staticmethod
    def _priceable(pool: PoolState) -> bool:
        """Whether ``pool`` may contribute rate edges to the search graph.

        A pool is priceable only when it is tradable (liquidity on both sides)
        **and** neither token is quarantined. Quarantined tokens
        (fee-on-transfer / rebasing) break the constant-product/AMM reserve
        invariant, so pricing them with this math would misstate — and can
        *overstate* — output; they are admitted to the pool map for provenance but
        never given an edge, so no detected cycle can route through them
        (``model/token.py``; CLAUDE.md §3, SECURITY §3).
        """
        return pool.tradable and not (pool.token0.quarantined or pool.token1.quarantined)

    def remove_pool(self, address: str) -> set[TokenKey]:
        """Remove a pool entirely (e.g. it went untradable or was invalidated)."""
        pool = self._pools.get(address)
        if pool is None:
            return set()
        # Remove edges *before* dropping the pool: _remove_pool_edges reads the
        # stored pool to learn which (src, dst) buckets to clear.
        self._remove_pool_edges(address)
        del self._pools[address]
        touched = {pool.token0.key, pool.token1.key}
        self._dirty |= touched
        return touched

    def _make_edge(self, pool: PoolState, src: TokenKey, dst: TokenKey) -> RateEdge:
        base_rate = quote.marginal_rate(pool, src)
        dec_in = pool.token0.decimals if src == pool.token0.key else pool.token1.decimals
        dec_out = pool.token1.decimals if src == pool.token0.key else pool.token0.decimals
        human_rate = base_rate * (10.0 ** (dec_in - dec_out))
        return RateEdge(src, dst, pool.address, human_rate, -math.log(human_rate))

    def _add_edge(self, edge: RateEdge) -> None:
        self._adj.setdefault(edge.src, {}).setdefault(edge.dst, {})[edge.pool] = edge

    def _remove_pool_edges(self, address: str) -> None:
        old = self._pools.get(address)
        if old is None:
            return
        for src, dst in ((old.token0.key, old.token1.key), (old.token1.key, old.token0.key)):
            bucket = self._adj.get(src, {}).get(dst)
            if bucket is not None:
                bucket.pop(address, None)
                if not bucket:
                    del self._adj[src][dst]
                    if not self._adj[src]:
                        del self._adj[src]

    # ------------------------------------------------------------------ #
    # Read / search views
    # ------------------------------------------------------------------ #
    def pool(self, address: str) -> PoolState:
        """The current :class:`PoolState` for ``address`` (for exact re-pricing)."""
        return self._pools[address]

    def tokens(self) -> set[TokenKey]:
        """All token nodes that currently have at least one outgoing edge."""
        return set(self._adj.keys())

    def out_edges(self, src: TokenKey) -> list[RateEdge]:
        """Every outgoing edge from ``src`` (all parallel pools included)."""
        return [e for bucket in self._adj.get(src, {}).values() for e in bucket.values()]

    def edges_between(self, src: TokenKey, dst: TokenKey) -> list[RateEdge]:
        """All parallel edges ``src -> dst`` (one per pool)."""
        return list(self._adj.get(src, {}).get(dst, {}).values())

    def all_edges(self) -> list[RateEdge]:
        """Every edge in the graph — the relaxation set for Bellman-Ford/SPFA."""
        return [
            e
            for by_dst in self._adj.values()
            for bucket in by_dst.values()
            for e in bucket.values()
        ]

    def best_edges(self) -> dict[TokenKey, dict[TokenKey, RateEdge]]:
        """Min-weight edge per ordered pair — collapses the multigraph for search.

        For negative-cycle search only the cheapest pool between two tokens can
        matter, so this view keeps one edge per ``(src, dst)``.
        """
        best: dict[TokenKey, dict[TokenKey, RateEdge]] = {}
        for src, by_dst in self._adj.items():
            row = best.setdefault(src, {})
            for dst, bucket in by_dst.items():
                row[dst] = min(bucket.values(), key=lambda e: e.log_weight)
        return best

    # ------------------------------------------------------------------ #
    # Dirty tracking
    # ------------------------------------------------------------------ #
    def pop_dirty(self) -> set[TokenKey]:
        """Return and clear the set of tokens touched since the last call."""
        dirty, self._dirty = self._dirty, set()
        return dirty

    @property
    def num_tokens(self) -> int:
        return len(self._adj)

    @property
    def num_edges(self) -> int:
        return sum(len(b) for by_dst in self._adj.values() for b in by_dst.values())
