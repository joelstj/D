"""Unit tests for cross-chain 2-hop spread detection."""

from __future__ import annotations

from typing import NamedTuple

import pytest

from graphkit import GraphKit
from l2arb.detect.cross_chain import (
    BridgeModel,
    BridgeQuote,
    StaticBridgeModel,
    cross_chain_two_hop,
)
from l2arb.detect.profit import ProfitContext
from l2arb.graph.rategraph import RateGraph
from l2arb.model.canonical_asset import AssetRegistry, AssetRepresentation, CanonicalAsset
from l2arb.model.opportunity import Opportunity, StrategyKind

pytestmark = pytest.mark.unit

ARB, BASE = 42161, 8453


class Env(NamedTuple):
    buy_graph: RateGraph
    sell_graph: RateGraph
    registry: AssetRegistry
    bridge: BridgeModel
    buy_ctx: ProfitContext
    sell_ctx: ProfitContext


def _setup(
    gk: type[GraphKit],
    *,
    weth_bridgeable: bool = True,
    spread_num: int = 1_100_000,
    weth_on_base: bool = True,
    gas_price_wei: int = 10**6,
) -> Env:
    num_x = gk.token(1, chain=ARB)  # USDC on Arbitrum
    weth_x = gk.token(2, chain=ARB)  # WETH on Arbitrum
    weth_y = gk.token(3, chain=BASE)  # WETH on Base
    num_y = gk.token(4, chain=BASE)  # USDC on Base

    buy_pool = gk.v2(10, num_x, weth_x, 1_000_000 * 10**18, 1000 * 10**18)  # cheap WETH
    sell_pool = gk.v2(11, weth_y, num_y, 1000 * 10**18, spread_num * 10**18)  # dearer WETH

    registry = AssetRegistry()
    weth_reps = [AssetRepresentation(weth_x, bridgeable=weth_bridgeable)]
    if weth_on_base:
        weth_reps.append(AssetRepresentation(weth_y, bridgeable=weth_bridgeable))
    registry.register(CanonicalAsset("WETH", tuple(weth_reps)))
    registry.register(
        CanonicalAsset("USDC", (AssetRepresentation(num_x), AssetRepresentation(num_y)))
    )
    bridge = StaticBridgeModel(
        {("WETH", ARB, BASE): BridgeQuote(fee_bps=10.0, fixed_fee=0, settle_seconds=600)}
    )
    ctx = gk.profit_ctx(gas_price_wei=gas_price_wei)
    return Env(
        gk.graph([buy_pool], chain=ARB),
        gk.graph([sell_pool], chain=BASE),
        registry,
        bridge,
        ctx,
        ctx,
    )


def _run(env: Env, *, min_profit_bps: float = 1.0, **over: str) -> Opportunity | None:
    return cross_chain_two_hop(
        env.buy_graph,
        env.sell_graph,
        asset_symbol=over.get("asset_symbol", "WETH"),
        numeraire_symbol=over.get("numeraire_symbol", "USDC"),
        registry=env.registry,
        bridge=env.bridge,
        buy_ctx=env.buy_ctx,
        sell_ctx=env.sell_ctx,
        min_profit_bps=min_profit_bps,
    )


def test_detects_profitable_cross_chain_spread(gk: type[GraphKit]) -> None:
    opp = _run(_setup(gk))
    assert opp is not None
    assert opp.strategy is StrategyKind.CROSS_CHAIN_TWO_HOP
    assert opp.is_cross_chain is True
    assert opp.chain_ids == tuple(sorted((ARB, BASE)))
    assert opp.net_profit > 0
    assert opp.bridge_cost > 0
    assert opp.settle_seconds == 600
    assert opp.hops == 2
    assert opp.net_profit == opp.output_amount - opp.input_amount - opp.gas_cost
    assert opp.gross_profit - opp.bridge_cost - opp.gas_cost == opp.net_profit


def test_cross_chain_risk_is_penalised(gk: type[GraphKit]) -> None:
    opp = _run(_setup(gk))
    assert opp is not None
    assert opp.risk.success_probability < 0.9  # below the single-chain base


def test_no_spread_no_opportunity(gk: type[GraphKit]) -> None:
    assert _run(_setup(gk, spread_num=1_000_000)) is None


def test_non_bridgeable_asset_blocks_detection(gk: type[GraphKit]) -> None:
    assert _run(_setup(gk, weth_bridgeable=False)) is None


def test_asset_absent_on_a_chain_returns_none(gk: type[GraphKit]) -> None:
    # WETH registered only on the buy chain -> no sell-side representation.
    assert _run(_setup(gk, weth_on_base=False)) is None


def test_unknown_symbols_return_none(gk: type[GraphKit]) -> None:
    env = _setup(gk)
    assert _run(env, asset_symbol="NOPE") is None
    assert _run(env, numeraire_symbol="NOPE") is None


def test_missing_bridge_quote_returns_none(gk: type[GraphKit]) -> None:
    env = _setup(gk)._replace(bridge=StaticBridgeModel({}))
    assert _run(env) is None


def test_missing_pool_returns_none(gk: type[GraphKit]) -> None:
    env = _setup(gk)._replace(sell_graph=gk.graph([], chain=BASE))
    assert _run(env) is None


def test_high_threshold_rejects(gk: type[GraphKit]) -> None:
    assert _run(_setup(gk), min_profit_bps=10_000_000.0) is None


def test_gas_wipes_out_the_spread(gk: type[GraphKit]) -> None:
    # Enormous gas makes the net non-positive even though the gross spread exists.
    assert _run(_setup(gk, gas_price_wei=10**30)) is None


def test_bridge_quote_math() -> None:
    q = BridgeQuote(fee_bps=10.0, fixed_fee=5, settle_seconds=60)
    assert q.cost(10_000) == 5 + 10  # 0.1% of 10000 + fixed 5
    assert q.net_after(10_000) == 10_000 - 15
    assert q.net_after(1) == 0  # cost exceeds amount -> clamped to 0


def test_base_bridge_model_is_abstract() -> None:
    with pytest.raises(NotImplementedError):
        BridgeModel().quote("WETH", ARB, BASE, 1)
