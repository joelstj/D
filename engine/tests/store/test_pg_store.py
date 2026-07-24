"""Unit tests for the async opportunity store (in-memory SQLite)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from l2arb.model.blockstamp import Blockstamp
from l2arb.model.opportunity import Leg, Opportunity, RiskAssessment, StrategyKind
from l2arb.model.token import Token
from l2arb.store.pg_store import OpportunityStore

pytestmark = pytest.mark.unit

CHAIN = 42161
BS = Blockstamp(chain_id=CHAIN, number=100, block_hash="0x" + "ab" * 32, timestamp=1)
A = Token(chain_id=CHAIN, address="0x" + "11" * 20, decimals=18, symbol="A")
B = Token(chain_id=CHAIN, address="0x" + "22" * 20, decimals=18, symbol="B")


def _opp(net: int, score: float, block: int = 100, cross: bool = False) -> Opportunity:
    return Opportunity(
        strategy=StrategyKind.CROSS_CHAIN_TWO_HOP if cross else StrategyKind.TWO_HOP,
        numeraire=A,
        input_amount=10**21,
        output_amount=10**21 + net,
        gross_profit=net,
        gas_cost=0,
        net_profit=net,
        profit_bps=float(net),
        legs=(Leg("0xp1", A, B, 10, 11), Leg("0xp2", B, A, 11, 10 + net)),
        blockstamp=Blockstamp(CHAIN, block, "0x" + "ab" * 32, 1),
        chain_ids=(CHAIN, 8453) if cross else (CHAIN,),
        risk=RiskAssessment(0.9, 0.7, 0.1),
        score=score,
    )


@pytest_asyncio.fixture
async def store() -> AsyncIterator[OpportunityStore]:
    s = OpportunityStore("sqlite+aiosqlite:///:memory:")
    await s.init_schema()
    yield s
    await s.close()


async def test_save_and_count(store: OpportunityStore) -> None:
    assert await store.count() == 0
    await store.save(_opp(net=10**20, score=50.0), detected_at=1_700_000_000)
    assert await store.count() == 1


async def test_save_many_and_recent_ordering(store: OpportunityStore) -> None:
    n = await store.save_many([_opp(1, 1.0), _opp(2, 2.0), _opp(3, 3.0)], detected_at=1000)
    assert n == 3
    await store.save(_opp(net=99, score=9.0), detected_at=2000)  # newer
    recent = await store.recent(limit=2)
    assert len(recent) == 2
    assert recent[0]["detected_at"] == 2000  # newest first
    assert recent[0]["net_profit"] == "99"  # big int preserved as text


async def test_top_by_score(store: OpportunityStore) -> None:
    await store.save_many([_opp(10, 10.0), _opp(20, 90.0), _opp(30, 50.0)], detected_at=1000)
    top = await store.top_by_score(limit=1)
    assert top[0]["score"] == 90.0


async def test_save_many_empty_is_noop(store: OpportunityStore) -> None:
    assert await store.save_many([], detected_at=1000) == 0
    assert await store.count() == 0


async def test_row_captures_scalar_fields(store: OpportunityStore) -> None:
    await store.save(_opp(net=10**30, score=5.0, block=12345, cross=True), detected_at=1000)
    row = (await store.recent(1))[0]
    assert row["strategy"] == "cross_chain_two_hop"
    assert row["is_cross_chain"] is True
    assert row["block_number"] == 12345
    assert row["net_profit"] == str(10**30)  # beyond 64-bit, preserved
    assert row["pools"] == "0xp1,0xp2"
    assert row["numeraire_address"] == A.address


def test_dialect_flag_for_sqlite() -> None:
    s = OpportunityStore("sqlite+aiosqlite:///:memory:")
    assert s.is_postgres is False


def test_non_memory_dsn_uses_default_pool(tmp_path: object) -> None:
    # A file-backed DSN takes the non-StaticPool path; engine build is lazy (no I/O).
    s = OpportunityStore(f"sqlite+aiosqlite:///{tmp_path}/store.db")
    assert s.is_postgres is False
