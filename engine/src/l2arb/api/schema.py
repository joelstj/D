"""JSON request/response schema for the language-agnostic integration surface.

Any backend — Rust, Go, TypeScript, Node, C#, … — integrates by sending one JSON
request (the live pool state its bots gathered, plus per-chain gas/price context)
and receiving the ranked top-N opportunities as JSON. Requests are validated with
``pydantic`` so malformed input fails loud at the boundary (no bad data reaches
the math). Big integers cross the boundary as **decimal strings** (see
:mod:`l2arb.store.serde`) so no consumer loses precision.

This module is transport-agnostic: the same schema backs the stdin/stdout batch
runner and the HTTP service.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from l2arb.config import get_settings
from l2arb.model.blockstamp import Blockstamp
from l2arb.model.opportunity import Opportunity
from l2arb.model.token import Token

__all__ = [
    "AssetSpec",
    "BridgeSpec",
    "ChainConfig",
    "CrossChainConfig",
    "DetectRequest",
    "opportunity_to_dict",
    "token_to_output",
]


class ChainConfig(BaseModel):
    """Per-chain gas/price context and detection thresholds."""

    chain_id: int
    gas_price_wei: int = 0
    l1_data_fee_wei: int = 0
    base_gas: int = 150_000
    per_hop_gas: int = 100_000
    # Omitting either field defers to the operator's L2ARB__GAS_SAFETY_MULTIPLIER
    # / L2ARB__MIN_PROFIT_BPS default (l2arb.config.Settings) — a caller that
    # sends an explicit value always wins; default_factory only fires when the
    # field is absent from the request.
    gas_safety_multiplier: float = Field(
        default_factory=lambda: get_settings().gas_safety_multiplier
    )
    min_profit_bps: float = Field(default_factory=lambda: float(get_settings().min_profit_bps))
    # numeraire-base-units per 1 native (gas-token) wei, keyed by lower-case token
    # address on this chain. A numeraire with no price cannot be gas-costed and is
    # rejected (conservative). ``default_native_price`` optionally covers the rest.
    native_price_in: dict[str, float] = Field(default_factory=dict)
    default_native_price: float | None = None
    hubs: list[str] = Field(default_factory=list)  # curated hub token addresses
    # Per-request freshness override (seconds); ``None`` defers to the operator's
    # ``L2ARB__MAX_POOL_AGE_SECONDS`` default (see l2arb.config.Settings).
    max_pool_age_seconds: int | None = Field(default=None, gt=0)


class AssetSpec(BaseModel):
    """A canonical asset and its per-chain representations for cross-chain."""

    symbol: str
    representations: list[dict[str, Any]]  # {token: <token dict>, native, bridgeable}


class BridgeSpec(BaseModel):
    symbol: str
    from_chain: int
    to_chain: int
    # Non-negative: a negative fee would make the bridge "pay" the trader (see
    # BridgeQuote's own __post_init__ guard in detect/cross_chain.py) — reject a
    # malformed quote here too so the request fails loud at the API boundary
    # rather than 500ing deeper inside BridgeQuote construction.
    fee_bps: float = Field(default=0.0, ge=0)
    fixed_fee: int = Field(default=0, ge=0)
    settle_seconds: int = Field(default=0, ge=0)


class CrossChainConfig(BaseModel):
    assets: list[AssetSpec] = Field(default_factory=list)
    bridges: list[BridgeSpec] = Field(default_factory=list)
    pairs: list[tuple[str, str]] = Field(default_factory=list)


class DetectRequest(BaseModel):
    """The full detection request: state + config."""

    top_n: int = 10
    # Omitting this defers to the operator's L2ARB__MAX_HOPS default.
    max_hops: int = Field(default_factory=lambda: get_settings().max_hops, ge=2, le=8)
    incremental: bool = False
    chains: list[ChainConfig] = Field(default_factory=list)
    pools: list[dict[str, Any]] = Field(default_factory=list)  # serde pool dicts
    cross_chain: CrossChainConfig | None = None
    # Unix seconds this request is evaluated "as of" — drives the freshness gate
    # (see ChainConfig.max_pool_age_seconds). Omit to use the server's real
    # clock; set explicitly so a test (or a replay) can pin a deterministic "now".
    now_ts: int | None = None


def token_to_output(token: Token) -> dict[str, Any]:
    return {
        "chain_id": token.chain_id,
        "address": token.address,
        "decimals": token.decimals,
        "symbol": token.symbol,
    }


def _blockstamp_out(bs: Blockstamp) -> dict[str, Any]:
    return {
        "chain_id": bs.chain_id,
        "number": bs.number,
        "hash": bs.block_hash,
        "timestamp": bs.timestamp,
    }


def opportunity_to_dict(opp: Opportunity) -> dict[str, Any]:
    """Serialize an opportunity to JSON-safe output (big ints as decimal strings)."""
    return {
        "strategy": opp.strategy.value,
        "numeraire": token_to_output(opp.numeraire),
        "input_amount": str(opp.input_amount),
        "output_amount": str(opp.output_amount),
        "gross_profit": str(opp.gross_profit),
        "gas_cost": str(opp.gas_cost),
        "bridge_cost": str(opp.bridge_cost),
        "price_drift_cost": str(opp.price_drift_cost),
        "net_profit": str(opp.net_profit),
        "profit_bps": opp.profit_bps,
        "expected_net": str(opp.expected_net),
        "score": opp.score,
        "hops": opp.hops,
        "chain_ids": list(opp.chain_ids),
        "is_cross_chain": opp.is_cross_chain,
        "settle_seconds": opp.settle_seconds,
        "verified": opp.verified,
        "block": _blockstamp_out(opp.blockstamp),
        "risk": {
            "success_probability": opp.risk.success_probability,
            "capture_ratio": opp.risk.capture_ratio,
            "frontrun_risk": opp.risk.frontrun_risk,
            "notes": list(opp.risk.notes),
        },
        "legs": [
            {
                "pool": leg.pool_address,
                "token_in": token_to_output(leg.token_in),
                "token_out": token_to_output(leg.token_out),
                "amount_in": str(leg.amount_in),
                "amount_out": str(leg.amount_out),
            }
            for leg in opp.legs
        ],
    }
