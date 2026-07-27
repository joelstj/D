"""(De)serialize on-chain state to JSON-safe dicts for caching & persistence.

The engine's state is real, block-stamped on-chain data; to cache it (Redis) or
snapshot it (warm start) we need a stable, lossless, language-neutral encoding.
Two rules make it safe:

* **Big integers as decimal strings.** Reserves reach 2**112 and ``sqrtPriceX96``
  ~2**160 — beyond JSON's 53-bit safe integer and beyond many languages' native
  ints. Encoding them as strings preserves every wei exactly across any consumer.
* **Round-trip identity.** ``pool_from_dict(pool_to_dict(p)) == p`` for every valid
  pool (property-tested), so nothing is lost or silently coerced.

This module is pure and has no I/O; the Redis/DB adapters compose it with a
transport. It is also the shape the language-agnostic ingestion boundary accepts.
"""

from __future__ import annotations

from typing import Any

from l2arb.errors import DataError, IngestError
from l2arb.model.blockstamp import Blockstamp
from l2arb.model.pool import (
    PoolKind,
    PoolState,
    StableSwapState,
    V2Reserves,
    V3Slot0,
    WeightedState,
)
from l2arb.model.token import Token

__all__ = [
    "blockstamp_from_dict",
    "blockstamp_to_dict",
    "pool_from_dict",
    "pool_to_dict",
    "token_from_dict",
    "token_to_dict",
]


def token_to_dict(token: Token) -> dict[str, Any]:
    return {
        "chain_id": token.chain_id,
        "address": token.address,
        "decimals": token.decimals,
        "symbol": token.symbol,
        "quarantined": token.quarantined,
    }


def token_from_dict(data: dict[str, Any]) -> Token:
    try:
        return Token(
            chain_id=int(data["chain_id"]),
            address=str(data["address"]),
            decimals=int(data["decimals"]),
            symbol=str(data.get("symbol", "")),
            quarantined=bool(data.get("quarantined", False)),
        )
    except (KeyError, TypeError, ValueError, DataError) as exc:
        raise IngestError(f"invalid token payload: {exc}") from exc


def blockstamp_to_dict(bs: Blockstamp) -> dict[str, Any]:
    return {
        "chain_id": bs.chain_id,
        "number": bs.number,
        "block_hash": bs.block_hash,
        "timestamp": bs.timestamp,
    }


def blockstamp_from_dict(data: dict[str, Any]) -> Blockstamp:
    try:
        return Blockstamp(
            chain_id=int(data["chain_id"]),
            number=int(data["number"]),
            block_hash=str(data["block_hash"]),
            timestamp=int(data["timestamp"]),
        )
    except (KeyError, TypeError, ValueError, DataError) as exc:
        raise IngestError(f"invalid blockstamp payload: {exc}") from exc


def pool_to_dict(pool: PoolState) -> dict[str, Any]:
    """Encode a pool to a JSON-safe dict (big integers as decimal strings)."""
    data: dict[str, Any] = {
        "address": pool.address,
        "kind": pool.kind.value,
        "fee_pips": pool.fee_pips,
        "verified": pool.verified,
        "token0": token_to_dict(pool.token0),
        "token1": token_to_dict(pool.token1),
        "blockstamp": blockstamp_to_dict(pool.blockstamp),
    }
    if pool.v2 is not None:
        data["v2"] = {"reserve0": str(pool.v2.reserve0), "reserve1": str(pool.v2.reserve1)}
    if pool.v3 is not None:
        v3d: dict[str, Any] = {
            "sqrt_price_x96": str(pool.v3.sqrt_price_x96),
            "tick": pool.v3.tick,
            "liquidity": str(pool.v3.liquidity),
        }
        # Optional active-range boundaries — omitted when absent so the round-trip
        # stays identity for pools the caller has not enriched with tick bounds.
        if pool.v3.sqrt_ratio_lower_x96 is not None:
            v3d["sqrt_ratio_lower_x96"] = str(pool.v3.sqrt_ratio_lower_x96)
        if pool.v3.sqrt_ratio_upper_x96 is not None:
            v3d["sqrt_ratio_upper_x96"] = str(pool.v3.sqrt_ratio_upper_x96)
        data["v3"] = v3d
    if pool.stable is not None:
        data["stable"] = {
            "balance0": str(pool.stable.balance0),
            "balance1": str(pool.stable.balance1),
            "amp": str(pool.stable.amp),
        }
    if pool.weighted is not None:
        data["weighted"] = {
            "balance0": str(pool.weighted.balance0),
            "balance1": str(pool.weighted.balance1),
            "weight0": str(pool.weighted.weight0),
            "weight1": str(pool.weighted.weight1),
        }
    return data


def pool_from_dict(data: dict[str, Any]) -> PoolState:
    """Decode a pool from :func:`pool_to_dict`'s shape. Raises on malformed input."""
    try:
        kind = PoolKind(data["kind"])
        v2 = v3 = stable = weighted = None
        if kind is PoolKind.CONSTANT_PRODUCT:
            raw = data["v2"]
            v2 = V2Reserves(reserve0=int(raw["reserve0"]), reserve1=int(raw["reserve1"]))
        elif kind is PoolKind.CONCENTRATED_LIQUIDITY:
            raw = data["v3"]
            lower = raw.get("sqrt_ratio_lower_x96")
            upper = raw.get("sqrt_ratio_upper_x96")
            v3 = V3Slot0(
                sqrt_price_x96=int(raw["sqrt_price_x96"]),
                tick=int(raw["tick"]),
                liquidity=int(raw["liquidity"]),
                sqrt_ratio_lower_x96=None if lower is None else int(lower),
                sqrt_ratio_upper_x96=None if upper is None else int(upper),
            )
        elif kind is PoolKind.STABLESWAP:
            raw = data["stable"]
            stable = StableSwapState(
                balance0=int(raw["balance0"]), balance1=int(raw["balance1"]), amp=int(raw["amp"])
            )
        else:
            raw = data["weighted"]
            weighted = WeightedState(
                balance0=int(raw["balance0"]),
                balance1=int(raw["balance1"]),
                weight0=int(raw["weight0"]),
                weight1=int(raw["weight1"]),
            )
        return PoolState(
            address=str(data["address"]),
            kind=kind,
            token0=token_from_dict(data["token0"]),
            token1=token_from_dict(data["token1"]),
            fee_pips=int(data["fee_pips"]),
            blockstamp=blockstamp_from_dict(data["blockstamp"]),
            v2=v2,
            v3=v3,
            stable=stable,
            weighted=weighted,
            verified=bool(data.get("verified", False)),
        )
    except (KeyError, TypeError, ValueError, DataError) as exc:
        raise IngestError(f"invalid pool payload: {exc}") from exc
