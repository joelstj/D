"""On-chain data-integrity verification against an independent oracle (CLAUDE.md §3).

This is the ``verify`` tier's core proof: the engine's AMM math reproduces **real,
live** L2 pool behaviour to the wei. The fixture in ``fixtures/base_onchain_pools.json``
holds pool state captured from Base (chain 8453) at a pinned block via raw
``eth_call`` (exact hex, not the lossy float path), each with **independent-oracle
probes** — the output an on-chain router/quoter contract returned for a given input
at that same block:

* Uniswap **V2** WETH/USDC — probes from ``UniswapV2Router02.getAmountsOut``.
* Uniswap **V3** WETH/USDC 0.05 % — probes from ``QuoterV2.quoteExactInputSingle``.

For every probe we rebuild the pool through the **real ingestion boundary**
(:func:`store.serde.pool_from_dict`, the same shape external bots feed) and assert
:func:`amm.quote.amount_out` equals the on-chain oracle value **bit-for-bit**. Two
independent sources (our math vs the deployed contract) agreeing at a specific
block is exactly the "verifiable" bar. The data is frozen for deterministic,
offline replay, so this runs in the normal gate as a permanent regression guard:
if the AMM math ever drifts from real chain behaviour, this goes red with a
concrete on-chain discrepancy.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from l2arb.amm import quote
from l2arb.model.token import Token
from l2arb.store.serde import pool_from_dict, pool_to_dict

pytestmark = pytest.mark.verify

_FIXTURE = Path(__file__).parent / "fixtures" / "base_onchain_pools.json"


def _load() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    return data


_DOC = _load()


def _pool_dict(pool: dict[str, Any]) -> dict[str, Any]:
    """Map a fixture pool onto the canonical serde ingestion shape."""
    chain_id = _DOC["chain_id"]

    def _tok(t: dict[str, Any]) -> dict[str, Any]:
        return {
            "chain_id": chain_id,
            "address": t["address"],
            "decimals": t["decimals"],
            "symbol": t["symbol"],
        }

    data: dict[str, Any] = {
        "address": pool["address"],
        "kind": pool["kind"],
        "fee_pips": pool["fee_pips"],
        # These pools ARE verified: two independent on-chain sources agree at the
        # pinned block (that is what the probes prove).
        "verified": True,
        "token0": _tok(pool["token0"]),
        "token1": _tok(pool["token1"]),
        "blockstamp": {
            "chain_id": chain_id,
            "number": _DOC["block_number"],
            "block_hash": _DOC["block_hash"],
            "timestamp": _DOC["block_timestamp"],
        },
    }
    state = pool["state"]
    if pool["kind"] == "v2":
        data["v2"] = {"reserve0": str(state["reserve0"]), "reserve1": str(state["reserve1"])}
    else:  # v3
        data["v3"] = {
            "sqrt_price_x96": str(state["sqrt_price_x96"]),
            "tick": state["tick"],
            "liquidity": str(state["liquidity"]),
        }
    return data


def _token_in(pool: dict[str, Any], probe: dict[str, Any]) -> Token:
    side = pool[probe["token_in"]]  # "token0" | "token1"
    return Token(
        chain_id=_DOC["chain_id"],
        address=side["address"],
        decimals=side["decimals"],
        symbol=side["symbol"],
    )


def _probe_cases() -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    cases: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for pool in _DOC["pools"]:
        for i, probe in enumerate(pool["probes"]):
            cases.append((f"{pool['label']} :: probe[{i}]", pool, probe))
    return cases


_CASES = _probe_cases()


def test_fixture_is_real_and_pinned() -> None:
    # Guard the provenance metadata so a careless edit can't turn this into a
    # synthetic test masquerading as on-chain verification.
    assert _DOC["chain_id"] == 8453
    assert _DOC["block_number"] > 0
    assert _DOC["block_hash"].startswith("0x")
    assert len(_DOC["block_hash"]) == 66
    assert _DOC["pools"], "expected at least one captured pool"
    assert all(p["probes"] for p in _DOC["pools"]), "every pool needs oracle probes"


@pytest.mark.parametrize(("label", "pool", "probe"), _CASES, ids=[c[0] for c in _CASES])
def test_engine_reproduces_onchain_quote_bit_for_bit(
    label: str, pool: dict[str, Any], probe: dict[str, Any]
) -> None:
    # Rebuild the pool through the real ingestion boundary, then price the exact
    # input and demand it equals what the deployed contract returned at the block.
    state = pool_from_dict(_pool_dict(pool))
    token_in = _token_in(pool, probe)
    got = quote.amount_out(state, token_in.key, probe["amount_in"])
    assert got == probe["amount_out"], (
        f"{label}: engine {got} != on-chain oracle {probe['amount_out']} "
        f"(input {probe['amount_in']} of {token_in.symbol})"
    )


@pytest.mark.parametrize("pool", _DOC["pools"], ids=[p["label"] for p in _DOC["pools"]])
def test_real_pool_ingestion_round_trips(pool: dict[str, Any]) -> None:
    # The captured real state survives the serialize/deserialize boundary losslessly
    # (big ints as decimal strings) — nothing is coerced or truncated on the way in.
    state = pool_from_dict(_pool_dict(pool))
    assert state.verified is True
    assert state.tradable is True
    assert pool_from_dict(pool_to_dict(state)) == state


@pytest.mark.parametrize(("label", "pool", "probe"), _CASES, ids=[c[0] for c in _CASES])
def test_marginal_rate_bounds_the_executed_rate(
    label: str, pool: dict[str, Any], probe: dict[str, Any]
) -> None:
    # A real swap's effective rate can never beat the (fee-inclusive) marginal rate:
    # price impact only ever costs the taker. This ties the graph-edge signal to the
    # exact math on real state, in the safe direction (no fabricated edge).
    state = pool_from_dict(_pool_dict(pool))
    token_in = _token_in(pool, probe)
    marginal = quote.marginal_rate(state, token_in.key)
    effective = probe["amount_out"] / probe["amount_in"]
    assert marginal > 0
    assert effective <= marginal * (1 + 1e-9), (
        f"{label}: effective {effective} exceeds marginal {marginal}"
    )
