"""Unit tests for the JSON detection service core."""

from __future__ import annotations

from typing import Any

import pytest

from graphkit import GraphKit
from l2arb.api.service import run_detection
from l2arb.store.serde import pool_to_dict, token_to_dict

pytestmark = pytest.mark.unit


def _single_chain_request(gk: type[GraphKit], *, price_numeraire: bool = True) -> dict[str, Any]:
    a, b = gk.token(1), gk.token(2)
    pools = [
        gk.v2(10, a, b, 1000 * 10**18, 1000 * 10**18),
        gk.v2(11, a, b, 1000 * 10**18, 1100 * 10**18),
    ]
    native_price_in = {a.address: 1.0, b.address: 1.0} if price_numeraire else {}
    return {
        "top_n": 10,
        "max_hops": 4,
        "chains": [
            {
                "chain_id": gk.CHAIN,
                "gas_price_wei": 10**6,
                "min_profit_bps": 1.0,
                "native_price_in": native_price_in,
            }
        ],
        "pools": [pool_to_dict(p) for p in pools],
    }


def test_finds_and_serializes_an_opportunity(gk: type[GraphKit]) -> None:
    resp = run_detection(_single_chain_request(gk))
    assert resp["count"] >= 1
    opp = resp["opportunities"][0]
    assert opp["strategy"] == "two_hop"
    assert int(opp["net_profit"]) > 0
    # Amounts are decimal strings (precision-safe across languages).
    assert isinstance(opp["input_amount"], str)
    assert opp["hops"] == 2
    assert len(opp["legs"]) == 2
    assert "success_probability" in opp["risk"]


def test_unpriced_numeraire_is_rejected(gk: type[GraphKit]) -> None:
    # Without an on-chain price for the numeraire, gas cannot be computed -> the
    # opportunity is charged infinite gas and never reported (conservative).
    resp = run_detection(_single_chain_request(gk, price_numeraire=False))
    assert resp["count"] == 0


def test_top_n_is_respected(gk: type[GraphKit]) -> None:
    req = _single_chain_request(gk)
    req["top_n"] = 0
    assert run_detection(req)["count"] == 0


def test_empty_request_returns_no_opportunities() -> None:
    resp = run_detection({"chains": [], "pools": []})
    assert resp["count"] == 0
    assert resp["opportunities"] == []
    # A timing block always rides along (observability); it never changes results.
    assert resp["timing"]["component"] == "engine"


def test_response_carries_engine_stage_timing(gk: type[GraphKit]) -> None:
    resp = run_detection(_single_chain_request(gk))
    timing = resp["timing"]
    assert timing["component"] == "engine"
    # The four engine stages are reported, in order, so a dashboard can attribute a
    # slow request to build / detect / rank / serialize rather than one opaque total.
    assert [s["stage"] for s in timing["stages"]] == ["build", "detect", "rank", "serialize"]
    for stage in timing["stages"]:
        assert isinstance(stage["ms"], float)
        assert stage["ms"] >= 0.0
    assert timing["total_ms"] >= 0.0
    # Timing is instrumentation only: dropping it leaves the exact same result set.
    assert resp["count"] >= 1


def test_cross_chain_via_service(gk: type[GraphKit]) -> None:
    arb, base = 42161, 8453
    num_x, weth_x = gk.token(1, chain=arb), gk.token(2, chain=arb)
    weth_y, num_y = gk.token(3, chain=base), gk.token(4, chain=base)
    buy = gk.v2(10, num_x, weth_x, 1_000_000 * 10**18, 1000 * 10**18)
    sell = gk.v2(11, weth_y, num_y, 1000 * 10**18, 1_100_000 * 10**18)
    req = {
        "top_n": 10,
        "chains": [
            {
                "chain_id": arb,
                "gas_price_wei": 10**6,
                "min_profit_bps": 1.0,
                "native_price_in": {num_x.address: 1.0},
            },
            {
                "chain_id": base,
                "gas_price_wei": 10**6,
                "min_profit_bps": 1.0,
                "native_price_in": {num_y.address: 1.0},
            },
        ],
        "pools": [pool_to_dict(buy), pool_to_dict(sell)],
        "cross_chain": {
            "assets": [
                {
                    "symbol": "WETH",
                    "representations": [
                        {"token": token_to_dict(weth_x)},
                        {"token": token_to_dict(weth_y)},
                    ],
                },
                {
                    "symbol": "USDC",
                    "representations": [
                        {"token": token_to_dict(num_x)},
                        {"token": token_to_dict(num_y)},
                    ],
                },
            ],
            "bridges": [
                {
                    "symbol": "WETH",
                    "from_chain": arb,
                    "to_chain": base,
                    "fee_bps": 10.0,
                    "fixed_fee": 0,
                    "settle_seconds": 600,
                }
            ],
            "pairs": [["WETH", "USDC"]],
        },
    }
    resp = run_detection(req)
    assert any(o["strategy"] == "cross_chain_two_hop" for o in resp["opportunities"])


def test_invalid_pool_payload_raises(gk: type[GraphKit]) -> None:
    from l2arb.errors import IngestError

    req = _single_chain_request(gk)
    req["pools"] = [{"kind": "v2"}]  # malformed
    with pytest.raises(IngestError):
        run_detection(req)
