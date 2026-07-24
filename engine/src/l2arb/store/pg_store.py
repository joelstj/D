"""Opportunity store — persist reported opportunities for analytics & backtest.

An async SQLAlchemy Core store. In production it points at **TimescaleDB**
(Postgres) and the opportunities table is a **hypertable** partitioned on
``detected_at`` for fast time-range analytics; the hypertable is created only on
Postgres (guarded by dialect), so the exact same code runs against SQLite in unit
tests without a live service.

The store is an adapter (ADR-002): the engine and backtester depend on the small
surface here, not on SQLAlchemy. Big-integer amounts (``net_profit``) are stored
as text to preserve every wei; scalar columns (strategy, score, block, chain) are
indexed for queries. ``detected_at`` is caller-supplied unix seconds — the store
does no wall-clock reads, so persistence stays deterministic and testable.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    func,
    insert,
    select,
    text,
)
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import StaticPool

from l2arb.model.opportunity import Opportunity

__all__ = ["OpportunityStore"]

_metadata = MetaData()

OPPORTUNITIES = Table(
    "opportunities",
    _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("detected_at", BigInteger, nullable=False, index=True),
    Column("strategy", String(32), nullable=False, index=True),
    Column("numeraire_chain", Integer, nullable=False),
    Column("numeraire_address", String(42), nullable=False),
    Column("net_profit", String(80), nullable=False),  # big int as decimal text
    Column("profit_bps", Float, nullable=False),
    Column("score", Float, nullable=False, index=True),
    Column("hops", Integer, nullable=False),
    Column("is_cross_chain", Boolean, nullable=False),
    Column("block_number", BigInteger, nullable=False),
    Column("chain_ids", String(128), nullable=False),
    Column("pools", String(1024), nullable=False),
)


def _row(opp: Opportunity, detected_at: int) -> dict[str, Any]:
    chain, address = opp.numeraire.key
    return {
        "detected_at": detected_at,
        "strategy": opp.strategy.value,
        "numeraire_chain": chain,
        "numeraire_address": address,
        "net_profit": str(opp.net_profit),
        "profit_bps": opp.profit_bps,
        "score": opp.score,
        "hops": opp.hops,
        "is_cross_chain": opp.is_cross_chain,
        "block_number": opp.blockstamp.number,
        "chain_ids": ",".join(str(c) for c in opp.chain_ids),
        "pools": ",".join(opp.pool_addresses),
    }


class OpportunityStore:
    """Async persistence for reported opportunities."""

    def __init__(self, dsn: str) -> None:
        # An in-memory SQLite DB lives only as long as its connection, so share a
        # single connection across the engine (StaticPool) — used by unit tests.
        if ":memory:" in dsn:
            self._engine: AsyncEngine = create_async_engine(
                dsn, poolclass=StaticPool, connect_args={"check_same_thread": False}
            )
        else:
            self._engine = create_async_engine(dsn)

    @property
    def is_postgres(self) -> bool:
        return self._engine.dialect.name == "postgresql"

    async def init_schema(self) -> None:
        """Create the table, and (on Postgres/Timescale) the hypertable."""
        async with self._engine.begin() as conn:
            await conn.run_sync(_metadata.create_all)
            if self.is_postgres:  # pragma: no cover - exercised only in the db tier
                await conn.execute(
                    text(
                        "SELECT create_hypertable('opportunities', 'detected_at', "
                        "chunk_time_interval => 86400, if_not_exists => TRUE, migrate_data => TRUE)"
                    )
                )

    async def save(self, opp: Opportunity, detected_at: int) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(insert(OPPORTUNITIES).values(**_row(opp, detected_at)))

    async def save_many(self, opps: Sequence[Opportunity], detected_at: int) -> int:
        if not opps:
            return 0
        rows = [_row(o, detected_at) for o in opps]
        async with self._engine.begin() as conn:
            await conn.execute(insert(OPPORTUNITIES), rows)
        return len(rows)

    async def recent(self, limit: int = 10) -> list[dict[str, Any]]:
        """The most recently detected opportunities (highest ``detected_at``)."""
        stmt = (
            select(OPPORTUNITIES)
            .order_by(OPPORTUNITIES.c.detected_at.desc(), OPPORTUNITIES.c.id.desc())
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            return [dict(row) for row in result.mappings()]

    async def top_by_score(self, limit: int = 10) -> list[dict[str, Any]]:
        stmt = select(OPPORTUNITIES).order_by(OPPORTUNITIES.c.score.desc()).limit(limit)
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            return [dict(row) for row in result.mappings()]

    async def count(self) -> int:
        async with self._engine.connect() as conn:
            result = await conn.execute(select(func.count()).select_from(OPPORTUNITIES))
            return int(result.scalar_one())

    async def close(self) -> None:
        await self._engine.dispose()
