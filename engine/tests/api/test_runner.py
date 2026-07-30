"""Unit tests for the stdin/stdout JSON batch runner."""

from __future__ import annotations

import io

import orjson
import pytest

from graphkit import GraphKit
from l2arb.api.runner import main, process
from l2arb.store.serde import pool_to_dict

pytestmark = pytest.mark.unit


def _request_bytes(gk: type[GraphKit]) -> bytes:
    a, b = gk.token(1), gk.token(2)
    pools = [
        gk.v2(10, a, b, 1000 * 10**18, 1000 * 10**18),
        gk.v2(11, a, b, 1000 * 10**18, 1100 * 10**18),
    ]
    return orjson.dumps(
        {
            "top_n": 5,
            "now_ts": gk.BS.timestamp,  # pin "now" to the fixture pools' own block time
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
    )


def test_process_success(gk: type[GraphKit]) -> None:
    response, code = process(_request_bytes(gk))
    assert code == 0
    assert response["count"] >= 1


def test_process_bad_json() -> None:
    response, code = process(b"{not json")
    assert code == 1
    assert "error" in response
    assert "type" in response


def test_process_validation_error() -> None:
    response, code = process(orjson.dumps({"max_hops": 99}))  # out of range
    assert code == 1
    assert "error" in response


def test_main_pipes_stdin_to_stdout(gk: type[GraphKit]) -> None:
    stdin = io.BytesIO(_request_bytes(gk))
    stdout = io.BytesIO()
    code = main(stdin=stdin, stdout=stdout)
    assert code == 0
    payload = orjson.loads(stdout.getvalue())
    assert payload["count"] >= 1
    assert payload["opportunities"][0]["strategy"] == "two_hop"


def test_main_configures_logging_on_every_invocation(monkeypatch: pytest.MonkeyPatch) -> None:
    import l2arb.api.runner as runner_module

    calls: list[None] = []
    monkeypatch.setattr(runner_module, "configure_logging", lambda: calls.append(None))
    main(stdin=io.BytesIO(b"garbage"), stdout=io.BytesIO())
    assert len(calls) == 1


def test_main_reports_errors_as_json() -> None:
    stdout = io.BytesIO()
    code = main(stdin=io.BytesIO(b"garbage"), stdout=stdout)
    assert code == 1
    assert "error" in orjson.loads(stdout.getvalue())
