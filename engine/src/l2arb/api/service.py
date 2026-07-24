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

from typing import Any

from l2arb.api.schema import AssetSpec, ChainConfig, DetectRequest, opportunity_to_dict
from l2arb.detect.cross_chain import BridgeQuote, StaticBridgeModel
from l2arb.detect.profit import GasCostFn, GasModel, ProfitContext
from l2arb.engine.engine import ArbitrageEngine
from l2arb.model.canonical_asset import AssetRegistry, AssetRepresentation, CanonicalAsset
from l2arb.model.token import TokenKey
from l2arb.store.serde import pool_from_dict, token_from_dict

__all__ = ["build_engine", "detect", "run_detection"]

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


def build_engine(request: DetectRequest) -> ArbitrageEngine:
    """Construct and populate an engine from a validated request."""
    engine = ArbitrageEngine(max_hops=request.max_hops)
    for cfg in request.chains:
        hubs = frozenset((cfg.chain_id, a.lower()) for a in cfg.hubs) or None
        engine.configure_chain(
            cfg.chain_id, ProfitContext(_gas_cost_fn(cfg), cfg.min_profit_bps), hubs
        )
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
    """Run detection for an already-validated request and return the response."""
    engine = build_engine(request)
    opportunities = engine.compute(top_n=request.top_n, incremental=request.incremental)
    return {
        "count": len(opportunities),
        "opportunities": [opportunity_to_dict(opp) for opp in opportunities],
    }


def run_detection(request_dict: dict[str, Any]) -> dict[str, Any]:
    """Validate a raw request dict, run detection, and return the JSON-safe response.

    Raises ``pydantic.ValidationError`` / :class:`~l2arb.errors.IngestError` on a
    malformed request — the caller (runner/HTTP) maps those to an error response.
    """
    return detect(DetectRequest.model_validate(request_dict))
