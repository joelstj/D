"""Round-trip + validation tests for state serialization."""

from __future__ import annotations

import pytest

from graphkit import GraphKit
from l2arb.errors import IngestError
from l2arb.store.serde import (
    blockstamp_from_dict,
    blockstamp_to_dict,
    pool_from_dict,
    pool_to_dict,
    token_from_dict,
    token_to_dict,
)

pytestmark = pytest.mark.unit


def test_token_round_trip(gk: type[GraphKit]) -> None:
    t = gk.token(1, decimals=6)
    assert token_from_dict(token_to_dict(t)) == t


def test_blockstamp_round_trip(gk: type[GraphKit]) -> None:
    bs = gk.blockstamp(chain=8453, number=999)
    assert blockstamp_from_dict(blockstamp_to_dict(bs)) == bs


def test_v2_pool_round_trip_preserves_big_ints(gk: type[GraphKit]) -> None:
    a, b = gk.token(1), gk.token(2)
    pool = gk.v2(10, a, b, 2**111, 3 * 10**30)  # beyond 64-bit
    data = pool_to_dict(pool)
    assert isinstance(data["v2"]["reserve0"], str)  # big ints encoded as strings
    assert pool_from_dict(data) == pool


def test_v3_pool_round_trip(gk: type[GraphKit]) -> None:
    a, b = gk.token(1), gk.token(2)
    pool = gk.v3(11, a, b, sqrt_price_x96=2**96, liquidity=10**24)
    assert pool_from_dict(pool_to_dict(pool)) == pool


def test_stableswap_pool_round_trip(gk: type[GraphKit]) -> None:
    a, b = gk.token(1), gk.token(2)
    pool = gk.stable(12, a, b, 10**24, 2 * 10**24, amp=500)
    data = pool_to_dict(pool)
    assert data["kind"] == "stable"
    assert pool_from_dict(data) == pool


def test_weighted_pool_round_trip(gk: type[GraphKit]) -> None:
    a, b = gk.token(1), gk.token(2)
    pool = gk.weighted(13, a, b, 10**24, 3 * 10**24, weight0=8 * 10**17, weight1=2 * 10**17)
    data = pool_to_dict(pool)
    assert data["kind"] == "weighted"
    assert pool_from_dict(data) == pool


def test_verified_flag_survives(gk: type[GraphKit]) -> None:
    from dataclasses import replace

    a, b = gk.token(1), gk.token(2)
    pool = replace(gk.v2(10, a, b, 10**18, 10**18), verified=True)
    assert pool_from_dict(pool_to_dict(pool)).verified is True


@pytest.mark.parametrize(
    "bad",
    [
        {},  # empty
        {"chain_id": "x", "address": "0x" + "11" * 20, "decimals": 18},  # bad chain
        {"chain_id": 1, "address": "not-an-address", "decimals": 18},  # bad address
    ],
)
def test_invalid_token_payload_raises(bad: dict[str, object]) -> None:
    with pytest.raises(IngestError):
        token_from_dict(bad)


def test_invalid_pool_payload_raises() -> None:
    with pytest.raises(IngestError, match="invalid pool payload"):
        pool_from_dict({"kind": "v2"})  # missing everything else


def test_invalid_blockstamp_payload_raises() -> None:
    with pytest.raises(IngestError, match="invalid blockstamp payload"):
        blockstamp_from_dict({"chain_id": 1})
