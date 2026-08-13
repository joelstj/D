"""Build an engine from a JSON request and run detection — the integration core.

This is the single place that turns a validated :class:`DetectRequest` into a
configured :class:`ArbitrageEngine`, runs a compute, and serializes the ranked
opportunities. Both the stdin/stdout runner and the HTTP service call
:func:`run_detection`; neither contains any engine logic of its own, so the
integration surface stays a thin, uniform boundary across every language.

Gas costing honours the data-integrity rule: a numeraire the caller did not price
cannot be gas-costed, so it is charged an effectively infinite gas cost and thus
never reported — the engine never invents a price.
"""

from __future__ import annotations

import time
from typing import Any

import structlog

from l2arb.api.schema import AssetSpec, ChainConfig, DetectRequest, opportunity_to_dict
from l2arb.config import get_settings
from l2arb.detect.cross_chain import BridgeQuote, StaticBridgeModel
from l2arb.detect.profit import GasCostFn, GasModel, ProfitContext
from l2arb.engine.engine import ArbitrageEngine
from l2arb.engine.ranking import rank_opportunities
from l2arb.model.canonical_asset import AssetRegistry, AssetRepresentation, CanonicalAsset
from l2arb.model.token import TokenKey
from l2arb.obs.latency import Stopwatch
from l2arb.store.serde import pool_from_dict, token_from_dict

__all__ = ["build_engine", "detect", "run_detection"]

_log = structlog.get_logger(__name__)

_UNPRICED_GAS = 10**36  # numeraire with no on-chain price -> reject the opportunity


def _gas_cost_fn(cfg: ChainConfig) -> GasCostFn:
    gas = GasModel(
        gas_price_wei=cfg.gas_price_wei,
        base_gas=cfg.base_gas,
        per_hop_gas=cfg.per_hop_gas,
        l1_data_fee_wei=cfg.l1_data_fee_wei,
        safety_multiplier=cfg.gas_safety_multiplier,
    )
    prices = {addr.lower(): float(p) for addr, p in cfg.native_price_in.items()}
    default = cfg.default_native_price
    if not prices and default is None:
        # Every numeraire on this chain is unpriced, so `cost` below returns the
        # _UNPRICED_GAS sentinel for all of them and the profit gate drops every
        # opportunity — the chain reports nothing, ever. Rejecting is correct (we
        # never invent a price), but doing it *silently* made a misconfigured
        # caller indistinguishable from a genuinely quiet market. Logged once per
        # chain per request, not inside `cost` (which is on the hot path).
        _log.warning(
            "chain_has_no_native_prices",
            chain_id=cfg.chain_id,
            detail=(
                "native_price_in is empty and default_native_price is unset, so no "
                "numeraire can be gas-costed and EVERY opportunity on this chain "
                "will be rejected as unprofitable. The caller (l2-ingest) derives "
                "this map from its `weth` / [chains.native_price_pools] config."
            ),
        )

    def cost(numeraire: TokenKey, hops: int) -> int:
        price = prices.get(numeraire[1], default)
        if price is None:
            return _UNPRICED_GAS
        return int(gas.cost_wei(hops) * price)

    return cost


def _registry(assets: list[AssetSpec]) -> AssetRegistry:
    registry = AssetRegistry()
    for spec in assets:
        reps = tuple(
            AssetRepresentation(
                token_from_dict(rep["token"]),
                native=bool(rep.get("native", True)),
                bridgeable=bool(rep.get("bridgeable", True)),
            )
            for rep in spec.representations
        )
        registry.register(CanonicalAsset(spec.symbol, reps))
    return registry


def build_engine(request: DetectRequest, *, now_ts: int) -> ArbitrageEngine:
    """Construct and populate an engine from a validated request.

    ``now_ts`` is the wall-clock instant (unix seconds) the request is evaluated
    "as of" — it drives the freshness gate (CLAUDE.md §3) via each chain's
    :class:`~l2arb.detect.profit.ProfitContext`. Required (not defaulted here) so
    this stays a pure function of its inputs; :func:`detect` resolves the real
    default.
    """
    engine = ArbitrageEngine(max_hops=request.max_hops)
    operator_max_age = get_settings().max_pool_age_seconds
    # E1: resolved once, uniformly, for every chain — like operator_max_age above,
    # this is a single operator-wide risk-model default (no per-request override
    # surface today), so the live /detect path always has the cross-chain
    # price-drift gate active (root CLAUDE.md §8 item 1's pattern).
    operator_price_drift = get_settings().cross_chain_price_drift_bps_per_minute
    for cfg in request.chains:
        hubs = frozenset((cfg.chain_id, a.lower()) for a in cfg.hubs) or None
        max_age = (
            cfg.max_pool_age_seconds if cfg.max_pool_age_seconds is not None else operator_max_age
        )
        ctx = ProfitContext(
            _gas_cost_fn(cfg),
            cfg.min_profit_bps,
            now_ts=now_ts,
            max_pool_age_seconds=max_age,
            price_drift_bps_per_minute=operator_price_drift,
        )
        engine.configure_chain(cfg.chain_id, ctx, hubs)
    for pool_dict in request.pools:
        engine.ingest(pool_from_dict(pool_dict))
    xc = request.cross_chain
    if xc is not None and xc.pairs:
        quotes = {
            (b.symbol, b.from_chain, b.to_chain): BridgeQuote(
                b.fee_bps, b.fixed_fee, b.settle_seconds
            )
            for b in xc.bridges
        }
        pairs = [(asset, num) for asset, num in xc.pairs]
        engine.configure_cross_chain(_registry(xc.assets), StaticBridgeModel(quotes), pairs)
    return engine


def detect(request: DetectRequest) -> dict[str, Any]:
    """Run detection for an already-validated request and return the response.

    The response carries an optional ``timing`` block (``build`` → ``detect`` →
    ``rank`` → ``serialize``, milliseconds) for the latency-health-check pipeline.
    It is pure instrumentation: measuring the stages never changes which
    opportunities are found or their order.

    ``request.now_ts`` pins the freshness gate's "now"; when absent (the normal
    HTTP/stdin caller), the server's real clock is used.
    """
    now_ts = request.now_ts if request.now_ts is not None else int(time.time())
    sw = Stopwatch()
    with sw.stage("build"):
        engine = build_engine(request, now_ts=now_ts)
    with sw.stage("detect"):
        found = engine.detect_all(incremental=request.incremental)
    with sw.stage("rank"):
        opportunities = rank_opportunities(found, request.top_n)
    with sw.stage("serialize"):
        serialized = [opportunity_to_dict(opp) for opp in opportunities]
    return {
        "count": len(opportunities),
        "opportunities": serialized,
        "timing": sw.to_dict(),
    }


def run_detection(request_dict: dict[str, Any]) -> dict[str, Any]:
    """Validate a raw request dict, run detection, and return the JSON-safe response.

    Raises ``pydantic.ValidationError`` / :class:`~l2arb.errors.IngestError` on a
    malformed request — the caller (runner/HTTP) maps those to an error response.
    """
    return detect(DetectRequest.model_validate(request_dict))
