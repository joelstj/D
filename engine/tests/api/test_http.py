"""Contract tests for the read-only HTTP service (in-process TestClient)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from graphkit import GraphKit
from l2arb.api.http import create_app
from l2arb.store.serde import pool_to_dict

pytestmark = pytest.mark.unit


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def _request(gk: type[GraphKit]) -> dict[str, Any]:
    a, b = gk.token(1), gk.token(2)
    pools = [
        gk.v2(10, a, b, 1000 * 10**18, 1000 * 10**18),
        gk.v2(11, a, b, 1000 * 10**18, 1100 * 10**18),
    ]
    return {
        "top_n": 10,
        "chains": [
            {
                "chain_id": gk.CHAIN,
                "gas_price_wei": 10**6,
                "min_profit_bps": 1.0,
                "native_price_in": {a.address: 1.0, b.address: 1.0},
            }
        ],
        "pools": [pool_to_dict(p) for p in pools],
    }


def test_health(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_detect_returns_opportunities(client: TestClient, gk: type[GraphKit]) -> None:
    resp = client.post("/detect", json=_request(gk))
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] >= 1
    assert body["opportunities"][0]["strategy"] == "two_hop"


def test_detect_rejects_malformed_request(client: TestClient) -> None:
    # max_hops out of the validated range -> 422 from FastAPI/pydantic.
    resp = client.post("/detect", json={"max_hops": 99})
    assert resp.status_code == 422


def test_detect_response_includes_engine_timing(client: TestClient, gk: type[GraphKit]) -> None:
    # The latency-health pipeline needs the engine's per-stage timing to survive the
    # HTTP boundary so the ingestion layer can relay it downstream.
    body = client.post("/detect", json=_request(gk)).json()
    timing = body["timing"]
    assert timing["component"] == "engine"
    assert [s["stage"] for s in timing["stages"]] == ["build", "detect", "rank", "serialize"]
