"""Unit tests for the async Redis pool cache (backed by an in-memory fake)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from graphkit import GraphKit
from l2arb.store.redis_cache import RedisPoolCache

pytestmark = pytest.mark.unit


class FakeRedis:
    """A minimal in-memory async stand-in for ``redis.asyncio.Redis``."""

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}

    async def get(self, key: str) -> bytes | None:
        return self.store.get(key)

    async def set(self, key: str, value: bytes) -> None:
        self.store[key] = value

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)


async def test_put_get_delete_round_trip(gk: type[GraphKit]) -> None:
    a, b = gk.token(1), gk.token(2)
    pool = gk.v2(10, a, b, 2**111, 3000 * 10**18)  # big int survives orjson<->str
    cache = RedisPoolCache(FakeRedis())
    assert await cache.put(pool) is True
    got = await cache.get(pool.chain_id, pool.address)
    assert got == pool
    await cache.delete(pool.chain_id, pool.address)
    assert await cache.get(pool.chain_id, pool.address) is None


async def test_missing_key_returns_none(gk: type[GraphKit]) -> None:
    cache = RedisPoolCache(FakeRedis())
    assert await cache.get(42161, "0x" + "aa" * 20) is None


async def test_stale_update_rejected(gk: type[GraphKit]) -> None:
    a, b = gk.token(1), gk.token(2)
    cache = RedisPoolCache(FakeRedis())
    fresh = replace(gk.v2(10, a, b, 10**18, 20 * 10**18), blockstamp=gk.blockstamp(number=100))
    older = replace(gk.v2(10, a, b, 10**18, 99 * 10**18), blockstamp=gk.blockstamp(number=50))
    assert await cache.put(fresh) is True
    assert await cache.put(older) is False
    got = await cache.get(fresh.chain_id, fresh.address)
    assert got is not None
    assert got.v2 is not None
    assert got.v2.reserve1 == 20 * 10**18  # fresher state retained


async def test_fresh_update_overwrites(gk: type[GraphKit]) -> None:
    a, b = gk.token(1), gk.token(2)
    cache = RedisPoolCache(FakeRedis())
    old = replace(gk.v2(10, a, b, 10**18, 10**18), blockstamp=gk.blockstamp(number=50))
    new = replace(gk.v2(10, a, b, 10**18, 5 * 10**18), blockstamp=gk.blockstamp(number=100))
    assert await cache.put(old) is True
    assert await cache.put(new) is True  # newer block -> overwrite
    got = await cache.get(new.chain_id, new.address)
    assert got is not None
    assert got.v2 is not None
    assert got.v2.reserve1 == 5 * 10**18


async def test_namespacing_isolates_keys(gk: type[GraphKit]) -> None:
    a, b = gk.token(1), gk.token(2)
    backend = FakeRedis()
    cache = RedisPoolCache(backend, namespace="test:pools")
    await cache.put(gk.v2(10, a, b, 10**18, 10**18))
    assert any(k.startswith("test:pools:") for k in backend.store)
