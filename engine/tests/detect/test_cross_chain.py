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
    num_y_decimals: int = 18,
    num_bridgeable: bool = True,
) -> Env:
    num_x = gk.token(1, chain=ARB)  # USDC on Arbitrum
    weth_x = gk.token(2, chain=ARB)  # WETH on Arbitrum
    weth_y = gk.token(3, chain=BASE)  # WETH on Base
    num_y = gk.token(4, decimals=num_y_decimals, chain=BASE)  # USDC on Base

    buy_pool = gk.v2(10, num_x, weth_x, 1_000_000 * 10**18, 1000 * 10**18)  # cheap WETH
    sell_pool = gk.v2(11, weth_y, num_y, 1000 * 10**18, spread_num * 10**18)  # dearer WETH

    registry = AssetRegistry()
    weth_reps = [AssetRepresentation(weth_x, bridgeable=weth_bridgeable)]
    if weth_on_base:
        weth_reps.append(AssetRepresentation(weth_y, bridgeable=weth_bridgeable))
    registry.register(CanonicalAsset("WETH", tuple(weth_reps)))
    registry.register(
        CanonicalAsset(
            "USDC",
            (
                AssetRepresentation(num_x, bridgeable=num_bridgeable),
                AssetRepresentation(num_y, bridgeable=num_bridgeable),
            ),
        )
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


def _run(
    env: Env,
    *,
    min_profit_bps: float = 1.0,
    price_drift_bps_per_minute: float | None = None,
    **over: str,
) -> Opportunity | None:
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
        price_drift_bps_per_minute=price_drift_bps_per_minute,
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


# ------------------------------- E4 regression ----------------------------- #
# The flat cross_chain_success_penalty was blind to settle_seconds. Pin that the
# real call site (_build_opportunity) now passes the bridge's actual
# settle_seconds through, so two spreads that are identical except for their
# bridge's settlement time get different (not identical) confidence haircuts.
def test_cross_chain_risk_penalty_scales_with_the_real_bridge_settle_seconds(
    gk: type[GraphKit],
) -> None:
    fast_bridge = StaticBridgeModel(
        {("WETH", ARB, BASE): BridgeQuote(fee_bps=10.0, fixed_fee=0, settle_seconds=30)}
    )
    slow_bridge = StaticBridgeModel(
        {("WETH", ARB, BASE): BridgeQuote(fee_bps=10.0, fixed_fee=0, settle_seconds=3600)}
    )
    env = _setup(gk)
    fast_opp = _run(env._replace(bridge=fast_bridge))
    slow_opp = _run(env._replace(bridge=slow_bridge))
    assert fast_opp is not None
    assert slow_opp is not None
    assert fast_opp.settle_seconds == 30
    assert slow_opp.settle_seconds == 3600
    assert slow_opp.risk.success_probability < fast_opp.risk.success_probability
    assert any(n.startswith("price_drift_risk_penalty=") for n in slow_opp.risk.notes)
    assert "settle_seconds=3600" in slow_opp.risk.notes
    assert "settle_seconds=30" in fast_opp.risk.notes


# ------------------------------- E1 regression ----------------------------- #
# The profit gate priced both legs off the same instant snapshot and subtracted
# only bridge + gas — nothing accounted for real price movement during the
# (non-atomic) settlement wait. These pin the new settle-time-scaled price-drift
# haircut: opt-in/None at this pure-compute layer (existing tests above that never
# pass price_drift_bps_per_minute must be completely unaffected), a real gate
# once configured.
def test_price_drift_cost_defaults_to_zero(gk: type[GraphKit]) -> None:
    opp = _run(_setup(gk))
    assert opp is not None
    assert opp.price_drift_cost == 0
    assert opp.net_profit == opp.output_amount - opp.input_amount - opp.gas_cost


def test_price_drift_cost_matches_formula(gk: type[GraphKit]) -> None:
    env = _setup(gk)
    rate = 3.0  # bps per minute
    opp = _run(env, price_drift_bps_per_minute=rate)
    assert opp is not None
    expected = int(opp.output_amount * rate * (opp.settle_seconds / 60.0) / 10_000.0)
    assert opp.price_drift_cost == expected
    assert opp.price_drift_cost > 0
    assert (
        opp.net_profit == opp.output_amount - opp.input_amount - opp.gas_cost - opp.price_drift_cost
    )
    assert (
        opp.gross_profit - opp.bridge_cost - opp.gas_cost - opp.price_drift_cost == opp.net_profit
    )


def test_price_drift_haircut_reduces_net_profit(gk: type[GraphKit]) -> None:
    env = _setup(gk)
    baseline = _run(env)
    drifted = _run(env, price_drift_bps_per_minute=5.0)
    assert baseline is not None
    assert drifted is not None
    # Sizing/AMM math is unaffected by the drift haircut (it is charged strictly
    # after sizing, like gas_cost) — only the profit/risk accounting differs.
    assert drifted.input_amount == baseline.input_amount
    assert drifted.output_amount == baseline.output_amount
    assert drifted.net_profit < baseline.net_profit
    assert drifted.profit_bps < baseline.profit_bps


def test_large_price_drift_can_reject_an_otherwise_profitable_spread(gk: type[GraphKit]) -> None:
    env = _setup(gk)
    assert _run(env) is not None  # sanity: profitable with the haircut off
    assert _run(env, price_drift_bps_per_minute=10_000.0) is None


def test_price_drift_cost_scales_with_settle_seconds(gk: type[GraphKit]) -> None:
    short_bridge = StaticBridgeModel(
        {("WETH", ARB, BASE): BridgeQuote(fee_bps=10.0, fixed_fee=0, settle_seconds=60)}
    )
    long_bridge = StaticBridgeModel(
        {("WETH", ARB, BASE): BridgeQuote(fee_bps=10.0, fixed_fee=0, settle_seconds=600)}
    )
    env = _setup(gk)
    rate = 2.0
    short_opp = _run(env._replace(bridge=short_bridge), price_drift_bps_per_minute=rate)
    long_opp = _run(env._replace(bridge=long_bridge), price_drift_bps_per_minute=rate)
    assert short_opp is not None
    assert long_opp is not None
    # settle_seconds is bridge-quote metadata only -> AMM sizing/output is
    # identical; only the haircut (and thus net_profit) should move.
    assert long_opp.output_amount == short_opp.output_amount
    assert long_opp.price_drift_cost > short_opp.price_drift_cost
    assert long_opp.net_profit < short_opp.net_profit


def test_no_spread_no_opportunity(gk: type[GraphKit]) -> None:
    assert _run(_setup(gk, spread_num=1_000_000)) is None


def test_non_bridgeable_asset_blocks_detection(gk: type[GraphKit]) -> None:
    assert _run(_setup(gk, weth_bridgeable=False)) is None


def test_asset_absent_on_a_chain_returns_none(gk: type[GraphKit]) -> None:
    # WETH registered only on the buy chain -> no sell-side representation.
    assert _run(_setup(gk, weth_on_base=False)) is None


# ------------------------------- E5 regression ----------------------------- #
# registry.are_fungible() gated the bridged asset but not the numeraire, which
# only got a strictly-weaker decimals-equality check (same decimals != same/
# fungible asset). These pin the fix: a numeraire pair that is decimals-equal
# but NOT registered as fungible must be rejected, mirroring the asset-side
# check immediately above.
def test_non_bridgeable_numeraire_blocks_detection(gk: type[GraphKit]) -> None:
    # Sanity baseline (bridgeable, matching decimals) detects; flipping only the
    # numeraire's bridgeable flag must now block it even though decimals still
    # match on both chains — before the fix, only decimals were checked, so this
    # spread would have been reported.
    assert _run(_setup(gk)) is not None
    assert _run(_setup(gk, num_bridgeable=False)) is None


def test_numeraire_with_only_one_side_bridgeable_is_rejected(gk: type[GraphKit]) -> None:
    # Asymmetric case: are_fungible() requires BOTH representations bridgeable
    # (AND semantics, model/canonical_asset.py::AssetRegistry.are_fungible). Pin
    # that the new check honours that — one bridgeable side is not enough, even
    # though decimals still match on both chains (18-dp default on both).
    num_x = gk.token(1, chain=ARB)
    weth_x = gk.token(2, chain=ARB)
    weth_y = gk.token(3, chain=BASE)
    num_y = gk.token(4, chain=BASE)

    buy_pool = gk.v2(10, num_x, weth_x, 1_000_000 * 10**18, 1000 * 10**18)
    sell_pool = gk.v2(11, weth_y, num_y, 1000 * 10**18, 1_100_000 * 10**18)

    registry = AssetRegistry()
    registry.register(
        CanonicalAsset("WETH", (AssetRepresentation(weth_x), AssetRepresentation(weth_y)))
    )
    registry.register(
        CanonicalAsset(
            "USDC",
            (
                AssetRepresentation(num_x, bridgeable=True),
                AssetRepresentation(num_y, bridgeable=False),
            ),
        )
    )
    bridge = StaticBridgeModel(
        {("WETH", ARB, BASE): BridgeQuote(fee_bps=10.0, fixed_fee=0, settle_seconds=600)}
    )
    ctx = gk.profit_ctx()
    env = Env(
        gk.graph([buy_pool], chain=ARB),
        gk.graph([sell_pool], chain=BASE),
        registry,
        bridge,
        ctx,
        ctx,
    )
    assert _run(env) is None


def test_mismatched_numeraire_decimals_across_chains_is_rejected_not_phantom(
    gk: type[GraphKit],
) -> None:
    # Regression: the cross-chain profit math (net = num_out - size) mixes the two
    # chains' numeraire base units. If the numeraire has different decimals across
    # chains (num_y here = 6-dp vs num_x = 18-dp), that unit mismatch used to
    # fabricate an enormous phantom profit stamped verified:true. It must now be
    # excluded before any pricing, never emitted.
    assert _run(_setup(gk, num_y_decimals=6)) is None
    # Sanity: with matching decimals the same setup detects a real spread.
    assert isinstance(_run(_setup(gk, num_y_decimals=18)), Opportunity)


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


def test_unverified_pool_on_either_leg_is_rejected(gk: type[GraphKit]) -> None:
    # CLAUDE.md §3: an unverified pool on the buy leg or the sell leg must veto
    # the whole cross-chain spread, even though it would otherwise be reported.
    env = _setup(gk)
    assert _run(env) is not None  # sanity: the baseline spread is reported

    unverified_buy = GraphKit.v2(
        10,
        gk.token(1, chain=ARB),
        gk.token(2, chain=ARB),
        1_000_000 * 10**18,
        1000 * 10**18,
        verified=False,
    )
    env_bad_buy = env._replace(buy_graph=gk.graph([unverified_buy], chain=ARB))
    assert _run(env_bad_buy) is None

    unverified_sell = GraphKit.v2(
        11,
        gk.token(3, chain=BASE),
        gk.token(4, chain=BASE),
        1000 * 10**18,
        1_100_000 * 10**18,
        verified=False,
    )
    env_bad_sell = env._replace(sell_graph=gk.graph([unverified_sell], chain=BASE))
    assert _run(env_bad_sell) is None


def test_stale_pool_on_either_leg_is_rejected_when_freshness_is_enforced(
    gk: type[GraphKit],
) -> None:
    from dataclasses import replace

    pool_ts = GraphKit.BS.timestamp
    env = _setup(gk)
    fresh_ctx = replace(env.buy_ctx, now_ts=pool_ts + 30, max_pool_age_seconds=60)
    stale_ctx = replace(env.buy_ctx, now_ts=pool_ts + 61, max_pool_age_seconds=60)

    assert _run(env._replace(buy_ctx=fresh_ctx, sell_ctx=fresh_ctx)) is not None
    assert _run(env._replace(buy_ctx=stale_ctx, sell_ctx=fresh_ctx)) is None
    assert _run(env._replace(buy_ctx=fresh_ctx, sell_ctx=stale_ctx)) is None


def test_bridge_quote_math() -> None:
    q = BridgeQuote(fee_bps=10.0, fixed_fee=5, settle_seconds=60)
    assert q.cost(10_000) == 5 + 10  # 0.1% of 10000 + fixed 5
    assert q.net_after(10_000) == 10_000 - 15
    assert q.net_after(1) == 0  # cost exceeds amount -> clamped to 0


def test_base_bridge_model_is_abstract() -> None:
    with pytest.raises(NotImplementedError):
        BridgeModel().quote("WETH", ARB, BASE, 1)
