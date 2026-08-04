"""Unit tests for the ArbitrageEngine facade."""

from __future__ import annotations

import pytest

from graphkit import GraphKit
from l2arb.engine.engine import ArbitrageEngine, top_hubs
from l2arb.model.pool import PoolState

pytestmark = pytest.mark.unit


def _two_hop_pools(gk: type[GraphKit], base: int, chain: int) -> list[PoolState]:
    a = gk.token(1, chain=chain)
    b = gk.token(2, chain=chain)
    return [
        gk.v2(base, a, b, 1000 * 10**18, 1000 * 10**18),
        gk.v2(base + 1, a, b, 1000 * 10**18, 1100 * 10**18),
    ]


def test_ingest_and_compute_returns_opportunities(gk: type[GraphKit]) -> None:
    engine = ArbitrageEngine()
    engine.configure_chain(gk.CHAIN, gk.profit_ctx())
    engine.ingest_many(_two_hop_pools(gk, 10, gk.CHAIN))
    opps = engine.compute(top_n=10)
    assert opps
    assert opps[0].net_profit > 0
    assert gk.CHAIN in engine.chain_ids


def test_unconfigured_chain_is_skipped(gk: type[GraphKit]) -> None:
    engine = ArbitrageEngine()
    # Ingest without configuring the chain: no gas/price context -> skipped.
    engine.ingest_many(_two_hop_pools(gk, 10, gk.CHAIN))
    assert engine.compute() == []


def test_top_n_limits_results(gk: type[GraphKit]) -> None:
    engine = ArbitrageEngine()
    engine.configure_chain(gk.CHAIN, gk.profit_ctx())
    engine.ingest_many(_two_hop_pools(gk, 10, gk.CHAIN))
    assert len(engine.compute(top_n=1)) <= 1


def test_incremental_mode_consumes_dirty(gk: type[GraphKit]) -> None:
    engine = ArbitrageEngine()
    engine.configure_chain(gk.CHAIN, gk.profit_ctx())
    engine.ingest_many(_two_hop_pools(gk, 10, gk.CHAIN))
    first = engine.compute(top_n=10, incremental=True)
    assert first  # freshly-ingested pools are dirty -> found
    # Nothing new ingested; incremental sees no dirty tokens -> no re-detection.
    second = engine.compute(top_n=10, incremental=True)
    assert second == []


def test_multi_chain_opportunities_ranked_together(gk: type[GraphKit]) -> None:
    engine = ArbitrageEngine()
    engine.configure_chain(42161, gk.profit_ctx())
    engine.configure_chain(8453, gk.profit_ctx())
    engine.ingest_many(_two_hop_pools(gk, 10, 42161))
    engine.ingest_many(_two_hop_pools(gk, 20, 8453))
    opps = engine.compute(top_n=10)
    chains = {c for o in opps for c in o.chain_ids}
    assert chains == {42161, 8453}


def test_top_hubs_picks_highest_degree(gk: type[GraphKit]) -> None:
    # 'a' connects to b and c (degree 2); b, c connect only back (degree 1 each).
    a, b, c = gk.token(1), gk.token(2), gk.token(3)
    graph = gk.graph(
        [
            gk.v2(10, a, b, 10**18, 10**18),
            gk.v2(11, a, c, 10**18, 10**18),
        ]
    )
    assert a.key in top_hubs(graph, 1)


def test_graph_accessor(gk: type[GraphKit]) -> None:
    engine = ArbitrageEngine()
    engine.configure_chain(gk.CHAIN, gk.profit_ctx())
    engine.ingest_many(_two_hop_pools(gk, 10, gk.CHAIN))
    assert engine.graph(gk.CHAIN).num_edges > 0


def test_cross_chain_detection_wired_in(gk: type[GraphKit]) -> None:
    from l2arb.detect.cross_chain import BridgeQuote, StaticBridgeModel
    from l2arb.model.canonical_asset import AssetRegistry, AssetRepresentation, CanonicalAsset
    from l2arb.model.opportunity import StrategyKind

    arb, base = 42161, 8453
    num_x, weth_x = gk.token(1, chain=arb), gk.token(2, chain=arb)
    weth_y, num_y = gk.token(3, chain=base), gk.token(4, chain=base)

    engine = ArbitrageEngine()
    engine.configure_chain(arb, gk.profit_ctx())
    engine.configure_chain(base, gk.profit_ctx())
    engine.ingest(gk.v2(10, num_x, weth_x, 1_000_000 * 10**18, 1000 * 10**18))  # cheap
    engine.ingest(gk.v2(11, weth_y, num_y, 1000 * 10**18, 1_100_000 * 10**18))  # dear

    registry = AssetRegistry()
    registry.register(
        CanonicalAsset("WETH", (AssetRepresentation(weth_x), AssetRepresentation(weth_y)))
    )
    registry.register(
        CanonicalAsset("USDC", (AssetRepresentation(num_x), AssetRepresentation(num_y)))
    )
    bridge = StaticBridgeModel(
        {("WETH", arb, base): BridgeQuote(fee_bps=10.0, fixed_fee=0, settle_seconds=600)}
    )
    engine.configure_cross_chain(registry, bridge, [("WETH", "USDC")])

    opps = engine.compute(top_n=10)
    assert any(o.strategy is StrategyKind.CROSS_CHAIN_TWO_HOP for o in opps)


def test_cross_chain_price_drift_context_is_threaded_to_the_opportunity(
    gk: type[GraphKit],
) -> None:
    # E1: _detect_cross_chain() must thread the buy-chain context's
    # price_drift_bps_per_minute through to cross_chain_two_hop, exactly like the
    # pre-existing min_profit_bps wiring immediately above it in engine.py.
    from dataclasses import replace

    from l2arb.detect.cross_chain import BridgeQuote, StaticBridgeModel
    from l2arb.model.canonical_asset import AssetRegistry, AssetRepresentation, CanonicalAsset
    from l2arb.model.opportunity import StrategyKind

    arb, base = 42161, 8453
    num_x, weth_x = gk.token(1, chain=arb), gk.token(2, chain=arb)
    weth_y, num_y = gk.token(3, chain=base), gk.token(4, chain=base)

    engine = ArbitrageEngine()
    # Only the buy-side (arb) chain gets a non-None rate; base stays at the
    # pure-compute default (None) to prove it's specifically the buy-chain
    # context that reaches cross_chain_two_hop, not just "some" context.
    engine.configure_chain(arb, replace(gk.profit_ctx(), price_drift_bps_per_minute=5.0))
    engine.configure_chain(base, gk.profit_ctx())
    engine.ingest(gk.v2(10, num_x, weth_x, 1_000_000 * 10**18, 1000 * 10**18))  # cheap
    engine.ingest(gk.v2(11, weth_y, num_y, 1000 * 10**18, 1_100_000 * 10**18))  # dear

    registry = AssetRegistry()
    registry.register(
        CanonicalAsset("WETH", (AssetRepresentation(weth_x), AssetRepresentation(weth_y)))
    )
    registry.register(
        CanonicalAsset("USDC", (AssetRepresentation(num_x), AssetRepresentation(num_y)))
    )
    bridge = StaticBridgeModel(
        {("WETH", arb, base): BridgeQuote(fee_bps=10.0, fixed_fee=0, settle_seconds=600)}
    )
    engine.configure_cross_chain(registry, bridge, [("WETH", "USDC")])

    opps = [o for o in engine.compute(top_n=10) if o.strategy is StrategyKind.CROSS_CHAIN_TWO_HOP]
    assert opps
    assert opps[0].price_drift_cost > 0


def test_cross_chain_disabled_by_default(gk: type[GraphKit]) -> None:
    engine = ArbitrageEngine()
    engine.configure_chain(gk.CHAIN, gk.profit_ctx())
    engine.ingest_many(_two_hop_pools(gk, 10, gk.CHAIN))
    # No configure_cross_chain call -> the cross-chain pass is a no-op.
    assert engine._detect_cross_chain() == []


def test_configure_with_explicit_hubs(gk: type[GraphKit]) -> None:
    a, b, c = gk.token(1), gk.token(2), gk.token(3)
    engine = ArbitrageEngine()
    engine.configure_chain(gk.CHAIN, gk.profit_ctx(), hubs=frozenset({a.key}))
    engine.ingest_many(
        [
            gk.v2(10, a, b, 1000 * 10**18, 1000 * 10**18),
            gk.v2(11, b, c, 1000 * 10**18, 1000 * 10**18),
            gk.v2(12, c, a, 1000 * 10**18, 1100 * 10**18),  # triangular edge
        ]
    )
    opps = engine.compute(top_n=10)
    # The configured hub 'a' roots the triangle detection.
    assert any(o.hops == 3 for o in opps)


def test_stale_ingest_is_dropped(gk: type[GraphKit]) -> None:
    from dataclasses import replace

    engine = ArbitrageEngine()
    engine.configure_chain(gk.CHAIN, gk.profit_ctx())
    a, b = gk.token(1), gk.token(2)
    fresh = replace(gk.v2(10, a, b, 10**18, 20 * 10**18), blockstamp=gk.blockstamp(number=100))
    older = replace(gk.v2(10, a, b, 10**18, 99 * 10**18), blockstamp=gk.blockstamp(number=50))
    assert engine.ingest(fresh) is True
    assert engine.ingest(older) is False  # stale -> dropped, graph unchanged


def test_snapshot_and_restore_warm_start(gk: type[GraphKit]) -> None:
    engine = ArbitrageEngine()
    engine.configure_chain(gk.CHAIN, gk.profit_ctx())
    applied = engine.ingest_many(_two_hop_pools(gk, 10, gk.CHAIN))
    assert applied == 2
    snap = engine.snapshot()
    assert len(snap) == 2

    revived = ArbitrageEngine()
    revived.configure_chain(gk.CHAIN, gk.profit_ctx())
    assert revived.load_snapshot(snap) == 2
    before = engine.compute(top_n=10)
    after = revived.compute(top_n=10)
    assert len(after) == len(before)
    assert after[0].net_profit == before[0].net_profit
