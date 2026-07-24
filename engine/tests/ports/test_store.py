"""The store ports are runtime-checkable and our adapters satisfy them."""

from __future__ import annotations

import pytest

from l2arb.ports.store import AsyncPoolCache, PoolCache
from l2arb.store.memory_cache import PoolStateCache
from l2arb.store.redis_cache import RedisPoolCache

pytestmark = pytest.mark.unit


class _FakeRedis:
    async def get(self, key: str) -> bytes | None:
        return None

    async def set(self, key: str, value: bytes) -> None:
        return None

    async def delete(self, key: str) -> None:
        return None


def test_memory_cache_is_a_pool_cache() -> None:
    assert isinstance(PoolStateCache(), PoolCache)


def test_redis_cache_is_an_async_pool_cache() -> None:
    assert isinstance(RedisPoolCache(_FakeRedis()), AsyncPoolCache)


def test_ports_are_runtime_checkable() -> None:
    # A bare object is not a pool cache.
    assert not isinstance(object(), PoolCache)
    assert not isinstance(object(), AsyncPoolCache)
