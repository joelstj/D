"""Redis-backed async hot cache for pool state (cross-process sharing).

Lets the per-chain ingestion bots and the calculation engine share one source of
live pool state, and gives the engine a warm cache to start from. Implements
:class:`~l2arb.ports.store.AsyncPoolCache` over any client matching :class:`RedisLike`
(``redis.asyncio.Redis`` does), so the module imports no concrete Redis at load
time and is unit-testable with an in-memory fake.

State is stored as ``orjson`` of the :mod:`l2arb.store.serde` dict shape — big
integers as strings, every wei preserved. Freshness is enforced read-then-write:
a stale update is a no-op. This is race-free under the intended **single writer
per chain** model (one ingestion bot per chain, per the system design); a
multi-writer deployment would need a Lua CAS, recorded as future work.
"""

from __future__ import annotations

from typing import Any, Protocol

import orjson

from l2arb.model.pool import PoolState
from l2arb.store.serde import pool_from_dict, pool_to_dict

__all__ = ["RedisLike", "RedisPoolCache"]


class RedisLike(Protocol):
    """The minimal async Redis surface this adapter needs."""

    async def get(self, key: str) -> bytes | None: ...

    async def set(self, key: str, value: bytes) -> Any: ...

    async def delete(self, key: str) -> Any: ...


class RedisPoolCache:
    """Async pool-state cache backed by Redis (or any :class:`RedisLike`)."""

    def __init__(self, client: RedisLike, namespace: str = "l2arb:pool") -> None:
        self._client = client
        self._namespace = namespace

    def _key(self, chain_id: int, address: str) -> str:
        return f"{self._namespace}:{chain_id}:{address.lower()}"

    async def put(self, pool: PoolState) -> bool:
        """Store ``pool`` iff at least as fresh as the cached entry (read-then-write)."""
        key = self._key(pool.chain_id, pool.address)
        existing = await self._client.get(key)
        if existing is not None:
            current = pool_from_dict(orjson.loads(existing))
            if not pool.blockstamp.is_same_or_newer(current.blockstamp):
                return False
        await self._client.set(key, orjson.dumps(pool_to_dict(pool)))
        return True

    async def get(self, chain_id: int, address: str) -> PoolState | None:
        raw = await self._client.get(self._key(chain_id, address))
        if raw is None:
            return None
        return pool_from_dict(orjson.loads(raw))

    async def delete(self, chain_id: int, address: str) -> None:
        await self._client.delete(self._key(chain_id, address))
