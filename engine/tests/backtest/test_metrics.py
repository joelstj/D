"""Unit tests for replay summary metrics."""

from __future__ import annotations

import pytest

from l2arb.backtest.metrics import summarize
from l2arb.backtest.replay import ReplayResult
from l2arb.model.blockstamp import Blockstamp
from l2arb.model.opportunity import Leg, Opportunity, RiskAssessment, StrategyKind
from l2arb.model.token import Token

pytestmark = pytest.mark.unit

CHAIN = 42161
BS = Blockstamp(CHAIN, 1, "0x" + "ab" * 32, 1)
A = Token(CHAIN, "0x" + "11" * 20, 18, "A")
B = Token(CHAIN, "0x" + "22" * 20, 18, "B")


def _opp(net: int, score: float, strategy: StrategyKind = StrategyKind.TWO_HOP) -> Opportunity:
    return Opportunity(
        strategy=strategy,
        numeraire=A,
        input_amount=10**21,
        output_amount=10**21 + net,
        gross_profit=net,
        gas_cost=0,
        net_profit=net,
        profit_bps=float(net),
        legs=(Leg("0xp1", A, B, 10, 11),),
        blockstamp=BS,
        chain_ids=(CHAIN,),
        risk=RiskAssessment(0.9, 0.7, 0.1),
        score=score,
    )


def test_summary_of_empty_replay() -> None:
    report = summarize([])
    assert report["blocks"] == 0
    assert report["opportunities"] == 0
    assert report["hit_rate"] == 0.0
    assert report["total_net_profit"] == "0"
    assert report["mean_top_score_decay"] == 0.0


def test_summary_aggregates() -> None:
    results = [
        ReplayResult(at=1, opportunities=(_opp(100, 50.0), _opp(80, 40.0))),
        ReplayResult(at=2, opportunities=()),  # a quiet block
        ReplayResult(at=3, opportunities=(_opp(200, 90.0, StrategyKind.TRIANGULAR),)),
    ]
    report = summarize(results)
    assert report["blocks"] == 3
    assert report["opportunities"] == 3
    assert report["blocks_with_opportunity"] == 2
    assert report["hit_rate"] == pytest.approx(2 / 3)
    assert report["total_net_profit"] == str(380)
    assert report["max_net_profit"] == "200"
    assert report["strategy_mix"] == {"two_hop": 2, "triangular": 1}
    assert report["max_profit_bps"] == 200.0


def test_score_decay_between_blocks() -> None:
    # Only adjacent blocks that both have opportunities count: (1->2) = +40. The
    # pairs touching the empty block 3 are skipped.
    results = [
        ReplayResult(at=1, opportunities=(_opp(1, 50.0),)),
        ReplayResult(at=2, opportunities=(_opp(1, 90.0),)),
        ReplayResult(at=3, opportunities=()),
        ReplayResult(at=4, opportunities=(_opp(1, 70.0),)),
    ]
    assert summarize(results)["mean_top_score_decay"] == pytest.approx(40.0)


def test_big_int_profit_reported_as_string() -> None:
    report = summarize([ReplayResult(at=1, opportunities=(_opp(10**30, 5.0),))])
    assert report["total_net_profit"] == str(10**30)
    assert report["mean_net_profit"] == str(10**30)
