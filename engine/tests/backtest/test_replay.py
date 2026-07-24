"""Unit tests for deterministic historical replay."""

from __future__ import annotations

import pytest

from graphkit import GraphKit
from l2arb.backtest.replay import BlockSnapshot, ReplayResult, replay
from l2arb.engine.engine import ArbitrageEngine

pytestmark = pytest.mark.unit


def _engine(gk: type[GraphKit]) -> ArbitrageEngine:
    engine = ArbitrageEngine(max_hops=4)
    engine.configure_chain(gk.CHAIN, gk.profit_ctx(min_bps=1.0))
    return engine


def test_replay_reports_per_block(gk: type[GraphKit]) -> None:
    a, b = gk.token(1), gk.token(2)
    # Block 1: an arbitrage exists (mispriced pair). Block 2: repriced to no arb.
    snap1 = BlockSnapshot(
        at=100,
        pools=(
            gk.v2(10, a, b, 1000 * 10**18, 1000 * 10**18),
            gk.v2(11, a, b, 1000 * 10**18, 1100 * 10**18),
        ),
    )
    snap2 = BlockSnapshot(
        at=101,
        pools=(gk.v2(11, a, b, 1000 * 10**18, 1000 * 10**18),),  # pool 11 repriced to 1:1
    )
    results = replay(_engine(gk), [snap1, snap2], top_n=10)
    assert [r.at for r in results] == [100, 101]
    assert results[0].opportunities  # arb at block 100
    assert not results[1].opportunities  # gone at block 101


def test_replay_is_deterministic(gk: type[GraphKit]) -> None:
    a, b = gk.token(1), gk.token(2)
    snaps = [
        BlockSnapshot(
            at=t,
            pools=(
                gk.v2(10, a, b, 1000 * 10**18, 1000 * 10**18),
                gk.v2(11, a, b, 1000 * 10**18, 1100 * 10**18),
            ),
        )
        for t in (1, 2, 3)
    ]
    r1 = replay(_engine(gk), snaps, top_n=5)
    r2 = replay(_engine(gk), snaps, top_n=5)
    assert [(x.at, x.opportunities[0].net_profit) for x in r1] == [
        (x.at, x.opportunities[0].net_profit) for x in r2
    ]


def test_replay_empty() -> None:
    assert replay(ArbitrageEngine(), []) == []


def test_replay_result_shape(gk: type[GraphKit]) -> None:
    a, b = gk.token(1), gk.token(2)
    snap = BlockSnapshot(at=5, pools=(gk.v2(10, a, b, 10**18, 10**18),))
    (result,) = replay(_engine(gk), [snap])
    assert isinstance(result, ReplayResult)
    assert result.at == 5
