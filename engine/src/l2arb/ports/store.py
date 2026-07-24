"""Typed ports for persistence & caching (the seams; adapters plug in behind them).

Keeping these as ``Protocol`` interfaces lets the engine cache and persist state
without knowing whether the backing store is an in-process dict, Redis, or
Postgres — a new store is an adapter, not a rewrite (ADR-002). All state that
flows through carries its :class:`~l2arb.model.blockstamp.Blockstamp`, so a cache
can reject stale writes and a store keeps provenance (CLAUDE.md §3).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from l2arb.model.pool import PoolState

__all__ = ["AsyncPoolCache", "PoolCache"]


@runtime_checkable
class PoolCache(Protocol):
    """A synchronous hot cache of the latest state per ``(chain_id, address)``.

    Pool addresses can collide across chains, so the key is the pair. Implementers
    must reject out-of-order updates (an older block must never overwrite fresher
    state) and return whether the write took effect.
    """

    def put(self, pool: PoolState) -> bool:
        """Store ``pool`` iff at least as fresh as the current entry.

        Returns ``True`` if the cache was updated, ``False`` on a stale no-op.
        """
        ...

    def get(self, chain_id: int, address: str) -> PoolState | None: ...

    def __len__(self) -> int: ...


@runtime_checkable
class AsyncPoolCache(Protocol):
    """Async variant for network-backed caches (e.g. Redis) on the hot path."""

    async def put(self, pool: PoolState) -> bool: ...

    async def get(self, chain_id: int, address: str) -> PoolState | None: ...

    async def delete(self, chain_id: int, address: str) -> None: ...
