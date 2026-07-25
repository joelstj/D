"""ArbitrageEngine — the plug-and-play calculation core.

This is the component external systems integrate against. Per-chain off-chain
bots feed it live, on-chain-verified pool state; it maintains a rate graph per
chain, runs every detector + the net-profit gate, and returns the **top-N**
risk-ranked opportunities — spanning same-chain 2-hop, triangular, cross-dex, and
bounded multi-hop (cross-chain 2-hop is layered on in Phase 5).

It is pure compute: **no I/O, no keys, no execution.** Data comes in via
:meth:`ingest`; gas/price context per chain comes via :meth:`configure_chain`
(both sourced on-chain by the caller). :meth:`compute` is deterministic given the
ingested state, so it is trivially testable and safe to call from any language
runtime through a thin transport (see the integration surface).

Two detection modes:

* **Full sweep** (default) — scan the whole current graph; returns *all* standing
  opportunities. Correct and simple; use it for request/response "give me the
  current top-N".
* **Incremental** — scan only the neighbourhoods of pools changed since the last
  compute (via the graph's dirty set); for the streaming hot path.
"""

from __future__ import annotations

from itertools import permutations
from typing import Any

from l2arb.detect.cross_chain import BridgeModel, cross_chain_two_hop
from l2arb.detect.profit import ProfitContext
from l2arb.engine.detection import detect_on_graph
from l2arb.engine.ranking import rank_opportunities
from l2arb.graph.rategraph import RateGraph
from l2arb.model.canonical_asset import AssetRegistry
from l2arb.model.opportunity import Opportunity
from l2arb.model.pool import PoolState
from l2arb.model.token import TokenKey
from l2arb.store.memory_cache import PoolStateCache
from l2arb.store.serde import pool_from_dict

__all__ = ["ArbitrageEngine", "top_hubs"]


def top_hubs(graph: RateGraph, k: int) -> frozenset[TokenKey]:
    """The ``k`` highest-degree tokens — a sensible default hub set per chain.

    Liquidity (and thus most triangular arbitrage) concentrates on a few tokens;
    when a curated hub list is not configured, the busiest nodes approximate it.
    """
    degree = {token: len(graph.out_edges(token)) for token in graph.tokens()}
    ordered = sorted(degree, key=lambda t: (degree[t], t), reverse=True)
    return frozenset(ordered[:k])


class ArbitrageEngine:
    """Stateful, single-process arbitrage calculation engine (detection only)."""

    def __init__(self, max_hops: int = 4, auto_hub_count: int = 8) -> None:
        self._max_hops = max_hops
        self._auto_hub_count = auto_hub_count
        self._graphs: dict[int, RateGraph] = {}
        self._contexts: dict[int, ProfitContext] = {}
        self._hubs: dict[int, frozenset[TokenKey]] = {}
        # Persistent working memory: latest fresh state per pool, snapshot-able.
        self._cache = PoolStateCache()
        # Cross-chain configuration (optional).
        self._registry: AssetRegistry | None = None
        self._bridge: BridgeModel | None = None
        self._xchain_pairs: tuple[tuple[str, str], ...] = ()

    # ------------------------------ configuration ------------------------- #
    def configure_chain(
        self,
        chain_id: int,
        ctx: ProfitContext,
        hubs: frozenset[TokenKey] | None = None,
    ) -> None:
        """Register a chain's gas/price context and (optionally) curated hubs.

        A chain must be configured before its opportunities are computed; the
        profit gate needs its on-chain gas/price context. ``hubs=None`` falls back
        to the busiest tokens at compute time.
        """
        self._contexts[chain_id] = ctx
        if hubs is not None:
            self._hubs[chain_id] = hubs
        self._graphs.setdefault(chain_id, RateGraph(chain_id))

    def configure_cross_chain(
        self,
        registry: AssetRegistry,
        bridge: BridgeModel,
        pairs: list[tuple[str, str]],
    ) -> None:
        """Enable cross-chain 2-hop detection for ``(asset, numeraire)`` pairs.

        ``pairs`` names the canonical asset/numeraire symbols to scan across every
        ordered pair of configured chains. Without this, ``compute`` stays
        single-chain.
        """
        self._registry = registry
        self._bridge = bridge
        self._xchain_pairs = tuple(pairs)

    # -------------------------------- ingest ------------------------------ #
    def ingest(self, pool: PoolState) -> bool:
        """Insert or update one pool's state; returns ``False`` on a stale no-op.

        Routed through the cache first: a stale update (older/equal block for a
        pool already seen) is dropped and never touches the graph, so late logs
        cannot corrupt fresher reserves.
        """
        if not self._cache.put(pool):
            return False
        graph = self._graphs.setdefault(pool.chain_id, RateGraph(pool.chain_id))
        graph.upsert_pool(pool)
        return True

    def ingest_many(self, pools: list[PoolState]) -> int:
        """Ingest a batch; returns how many were applied (non-stale)."""
        return sum(1 for pool in pools if self.ingest(pool))

    # ------------------------------ persistence --------------------------- #
    def snapshot(self) -> list[dict[str, Any]]:
        """A JSON-safe snapshot of all cached pool state for warm-start restore."""
        return self._cache.snapshot()

    def load_snapshot(self, rows: list[dict[str, Any]]) -> int:
        """Restore cached pool state from :meth:`snapshot`; returns pools applied.

        Rebuilds the per-chain graphs from the snapshot so a restarted engine
        resumes without a full cold re-sync. Chains still need their gas/price
        context configured before :meth:`compute`.
        """
        return sum(1 for row in rows if self.ingest(pool_from_dict(row)))

    # ------------------------------- compute ------------------------------ #
    def detect_all(self, incremental: bool = False) -> list[Opportunity]:
        """Run every detector across all configured chains; return the *unranked* finds.

        This is the detection half of :meth:`compute` — identical work, minus the
        final ranking — exposed so a caller can time detection and ranking as
        separate stages (the latency-health pipeline) without re-implementing the
        per-chain sweep. :meth:`compute` delegates to it, so behaviour cannot drift.

        ``incremental=True`` restricts each chain's search to pools changed since
        the previous call (consuming the dirty set); the default full sweep scans
        all standing opportunities.
        """
        found: list[Opportunity] = []
        for chain_id, graph in self._graphs.items():
            ctx = self._contexts.get(chain_id)
            if ctx is None:
                continue  # chain not configured with gas/price context yet
            sources = graph.pop_dirty() if incremental else None
            hubs = self._hubs.get(chain_id) or top_hubs(graph, self._auto_hub_count)
            found.extend(
                detect_on_graph(graph, hubs=hubs, max_hops=self._max_hops, ctx=ctx, sources=sources)
            )
        found.extend(self._detect_cross_chain())
        return found

    def compute(self, top_n: int = 10, incremental: bool = False) -> list[Opportunity]:
        """Detect, net-price, de-duplicate, and rank the top ``top_n`` opportunities.

        ``incremental=True`` restricts each chain's search to pools changed since
        the previous call (consuming the dirty set); the default full sweep
        returns all standing opportunities.
        """
        return rank_opportunities(self.detect_all(incremental=incremental), top_n)

    def _detect_cross_chain(self) -> list[Opportunity]:
        """Scan every configured (asset, numeraire) pair across ordered chain pairs."""
        if self._registry is None or self._bridge is None or not self._xchain_pairs:
            return []
        configured = [c for c in self._graphs if c in self._contexts]
        out: list[Opportunity] = []
        for buy_chain, sell_chain in permutations(configured, 2):
            for asset_symbol, numeraire_symbol in self._xchain_pairs:
                opp = cross_chain_two_hop(
                    self._graphs[buy_chain],
                    self._graphs[sell_chain],
                    asset_symbol=asset_symbol,
                    numeraire_symbol=numeraire_symbol,
                    registry=self._registry,
                    bridge=self._bridge,
                    buy_ctx=self._contexts[buy_chain],
                    sell_ctx=self._contexts[sell_chain],
                    min_profit_bps=self._contexts[buy_chain].min_profit_bps,
                )
                if opp is not None:
                    out.append(opp)
        return out

    # ------------------------------- introspection ------------------------ #
    @property
    def chain_ids(self) -> tuple[int, ...]:
        return tuple(sorted(self._graphs))

    def graph(self, chain_id: int) -> RateGraph:
        return self._graphs[chain_id]
