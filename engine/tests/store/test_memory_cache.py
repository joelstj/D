"""Unit tests for the in-process pool-state cache."""

from __future__ import annotations

from dataclasses import replace

import pytest

from graphkit import GraphKit
from l2arb.store.memory_cache import PoolStateCache

pytestmark = pytest.mark.unit


def test_put_and_get(gk: type[GraphKit]) -> None:
    a, b = gk.token(1), gk.token(2)
    pool = gk.v2(10, a, b, 10**18, 10**18)
    cache = PoolStateCache()
    assert cache.put(pool) is True
    assert cache.get(pool.chain_id, pool.address) == pool
    assert len(cache) == 1
    assert (pool.chain_id, pool.address.lower()) in cache


def test_stale_update_is_rejected(gk: type[GraphKit]) -> None:
    a, b = gk.token(1), gk.token(2)
    cache = PoolStateCache()
    fresh = replace(gk.v2(10, a, b, 10**18, 20 * 10**18), blockstamp=gk.blockstamp(number=100))
    older = replace(gk.v2(10, a, b, 10**18, 99 * 10**18), blockstamp=gk.blockstamp(number=50))
    assert cache.put(fresh) is True
    assert cache.put(older) is False  # older block -> no-op
    assert cache.get(fresh.chain_id, fresh.address).v2.reserve1 == 20 * 10**18  # type: ignore[union-attr]


def test_same_block_update_applies(gk: type[GraphKit]) -> None:
    a, b = gk.token(1), gk.token(2)
    cache = PoolStateCache()
    cache.put(gk.v2(10, a, b, 10**18, 10**18))
    # Same block number counts as fresh-enough (>=), so it applies.
    assert cache.put(gk.v2(10, a, b, 10**18, 2 * 10**18)) is True


def test_addresses_collide_across_chains(gk: type[GraphKit]) -> None:
    # Same pool address, different chains -> two distinct entries.
    a1, b1 = gk.token(1, chain=42161), gk.token(2, chain=42161)
    a2, b2 = gk.token(1, chain=8453), gk.token(2, chain=8453)
    cache = PoolStateCache()
    cache.put(gk.v2(10, a1, b1, 10**18, 10**18))
    cache.put(gk.v2(10, a2, b2, 10**18, 10**18))  # same address, other chain
    assert len(cache) == 2


def test_evict_stale(gk: type[GraphKit]) -> None:
    a, b = gk.token(1), gk.token(2)
    cache = PoolStateCache()
    pool = gk.v2(10, a, b, 10**18, 10**18)  # blockstamp ts = 1_700_000_000
    cache.put(pool)
    assert cache.evict_stale(now_ts=1_700_000_000 + 100, max_age_seconds=1000) == 0
    assert cache.evict_stale(now_ts=1_700_000_000 + 2000, max_age_seconds=1000) == 1
    assert len(cache) == 0


def test_snapshot_round_trip(gk: type[GraphKit]) -> None:
    a, b, c = gk.token(1), gk.token(2), gk.token(3)
    cache = PoolStateCache()
    cache.put(gk.v2(10, a, b, 10**18, 3000 * 10**18))
    cache.put(gk.v3(11, b, c, 2**96, 10**24))
    restored = PoolStateCache.from_snapshot(cache.snapshot())
    assert len(restored) == 2
    assert {p.address for p in restored.all_pools()} == {p.address for p in cache.all_pools()}
