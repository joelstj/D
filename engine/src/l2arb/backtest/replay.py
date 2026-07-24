"""Deterministic historical replay of the detection engine (offline analytics).

Feed a sequence of block snapshots (each the real, on-chain-verified pool state at
a block) through a configured :class:`ArbitrageEngine` and collect the
opportunities it would have reported at each block. This is **strictly offline
research** — it is never imported by the runtime detection path (enforced by a
scope-guard test, T-0905) — and it is deterministic: snapshots carry their own
timestamps, so a replay reads no wall-clock and reproduces exactly.

Snapshots are real historical state, not synthetic (docs/DATA_INTEGRITY §3): the
caller supplies them from an archival source; the replay just drives the same
engine used in production over them.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from l2arb.engine.engine import ArbitrageEngine
from l2arb.model.opportunity import Opportunity
from l2arb.model.pool import PoolState

__all__ = ["BlockSnapshot", "ReplayResult", "replay"]


@dataclass(frozen=True, slots=True)
class BlockSnapshot:
    """The pool state to apply at one point in history.

    ``at`` is the snapshot's time key (block number or unix seconds) — used only as
    a label and for time-series metrics, never read from a clock.
    """

    at: int
    pools: tuple[PoolState, ...]


@dataclass(frozen=True, slots=True)
class ReplayResult:
    """The opportunities the engine reported after applying one snapshot."""

    at: int
    opportunities: tuple[Opportunity, ...]


def replay(
    engine: ArbitrageEngine,
    snapshots: Iterable[BlockSnapshot],
    *,
    top_n: int = 10,
    incremental: bool = False,
) -> list[ReplayResult]:
    """Drive ``engine`` over ``snapshots`` in order, returning per-snapshot results.

    The engine carries state across snapshots (pools update incrementally, exactly
    as in production), so ``incremental=True`` mirrors the streaming hot path.
    """
    results: list[ReplayResult] = []
    for snapshot in snapshots:
        engine.ingest_many(list(snapshot.pools))
        opportunities = engine.compute(top_n=top_n, incremental=incremental)
        results.append(ReplayResult(snapshot.at, tuple(opportunities)))
    return results
