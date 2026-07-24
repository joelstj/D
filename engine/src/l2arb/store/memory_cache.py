"""In-process pool-state cache — the engine's fast, persistent working memory.

Holds the latest :class:`PoolState` per ``(chain_id, address)`` with two
guarantees the detection path relies on:

* **Freshness / monotonicity** — a stale update (an older or equal block for a
  pool already seen) is a no-op, so a late-arriving log can never overwrite
  fresher reserves (uses :meth:`Blockstamp.is_same_or_newer`).
* **Warm-start persistence** — :meth:`snapshot` produces a JSON-safe list and
  :meth:`from_snapshot` rebuilds the cache, so the engine can survive a restart
  without a full cold re-sync ("persistent memory").

Pure and synchronous; the async Redis adapter mirrors it for cross-process sharing.
"""

from __future__ import annotations

from typing import Any

from l2arb.model.pool import PoolState
from l2arb.store.serde import pool_from_dict, pool_to_dict

__all__ = ["PoolStateCache"]

_Key = tuple[int, str]


class PoolStateCache:
    """A latest-state-per-pool cache with freshness and snapshot/restore."""

    def __init__(self) -> None:
        self._pools: dict[_Key, PoolState] = {}

    @staticmethod
    def _key(chain_id: int, address: str) -> _Key:
        return (chain_id, address.lower())

    def put(self, pool: PoolState) -> bool:
        """Store ``pool`` iff it is at least as fresh as the current entry."""
        key = self._key(pool.chain_id, pool.address)
        current = self._pools.get(key)
        if current is not None and not pool.blockstamp.is_same_or_newer(current.blockstamp):
            return False  # stale update — keep the fresher state
        self._pools[key] = pool
        return True

    def get(self, chain_id: int, address: str) -> PoolState | None:
        return self._pools.get(self._key(chain_id, address))

    def all_pools(self) -> list[PoolState]:
        """Every cached pool — the warm-start feed for the engine's graphs."""
        return list(self._pools.values())

    def evict_stale(self, now_ts: int, max_age_seconds: int) -> int:
        """Drop pools whose block is older than ``max_age_seconds`` at ``now_ts``.

        Returns the number evicted. Keeps the cache from serving state that has
        aged past the freshness bound (a reorg/downtime safety valve).
        """
        stale = [
            key
            for key, pool in self._pools.items()
            if pool.blockstamp.is_stale(now_ts, max_age_seconds)
        ]
        for key in stale:
            del self._pools[key]
        return len(stale)

    def snapshot(self) -> list[dict[str, Any]]:
        """A JSON-safe snapshot of all cached pools for persistence."""
        return [pool_to_dict(pool) for pool in self._pools.values()]

    @classmethod
    def from_snapshot(cls, rows: list[dict[str, Any]]) -> PoolStateCache:
        """Rebuild a cache from :meth:`snapshot` output (warm start)."""
        cache = cls()
        for row in rows:
            cache.put(pool_from_dict(row))
        return cache

    def __len__(self) -> int:
        return len(self._pools)

    def __contains__(self, key: _Key) -> bool:
        return key in self._pools
