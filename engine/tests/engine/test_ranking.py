"""Unit tests for opportunity de-duplication and top-N ranking."""

from __future__ import annotations

import pytest

from l2arb.engine.ranking import rank_opportunities
from l2arb.model.blockstamp import Blockstamp
from l2arb.model.opportunity import Leg, Opportunity, RiskAssessment, StrategyKind
from l2arb.model.token import Token

pytestmark = pytest.mark.unit

CHAIN = 42161
BS = Blockstamp(chain_id=CHAIN, number=1, block_hash="0x" + "ab" * 32, timestamp=1)
A = Token(chain_id=CHAIN, address="0x" + "11" * 20, decimals=18, symbol="A")
B = Token(chain_id=CHAIN, address="0x" + "22" * 20, decimals=18, symbol="B")


def _opp(pools: tuple[str, ...], score: float, net: int = 100, numeraire: Token = A) -> Opportunity:
    legs = tuple(Leg(p, A, B, 10, 11) for p in pools)
    return Opportunity(
        strategy=StrategyKind.TWO_HOP,
        numeraire=numeraire,
        input_amount=1000,
        output_amount=1000 + net,
        gross_profit=net,
        gas_cost=0,
        net_profit=net,
        profit_bps=float(net),
        legs=legs,
        blockstamp=BS,
        chain_ids=(CHAIN,),
        risk=RiskAssessment(0.9, 0.7, 0.1),
        score=score,
    )


def test_orders_by_score_descending() -> None:
    a = _opp(("p1", "p2"), score=10.0)
    b = _opp(("p3", "p4"), score=50.0)
    c = _opp(("p5", "p6"), score=30.0)
    ranked = rank_opportunities([a, b, c], top_n=10)
    assert [o.score for o in ranked] == [50.0, 30.0, 10.0]


def test_top_n_truncates() -> None:
    opps = [_opp((f"p{i}", f"q{i}"), score=float(i)) for i in range(20)]
    ranked = rank_opportunities(opps, top_n=10)
    assert len(ranked) == 10
    assert ranked[0].score == 19.0


def test_dedup_keeps_best_scored() -> None:
    lo = _opp(("p1", "p2"), score=10.0, net=50)
    hi = _opp(("p2", "p1"), score=40.0, net=80)  # same pool set, higher score
    # Either arrival order must keep the higher-scored instance.
    assert rank_opportunities([lo, hi], top_n=10)[0].score == 40.0
    assert rank_opportunities([hi, lo], top_n=10)[0].score == 40.0
    assert len(rank_opportunities([lo, hi], top_n=10)) == 1


def test_same_pools_different_numeraire_not_deduped() -> None:
    x = _opp(("p1", "p2"), score=10.0, numeraire=A)
    y = _opp(("p1", "p2"), score=20.0, numeraire=B)
    assert len(rank_opportunities([x, y], top_n=10)) == 2


def test_non_positive_top_n_is_empty() -> None:
    assert rank_opportunities([_opp(("p1", "p2"), 5.0)], top_n=0) == []
    assert rank_opportunities([_opp(("p1", "p2"), 5.0)], top_n=-3) == []


def test_tie_broken_by_net_profit_deterministically() -> None:
    a = _opp(("p1", "p2"), score=10.0, net=100)
    b = _opp(("p3", "p4"), score=10.0, net=200)
    ranked = rank_opportunities([a, b], top_n=10)
    assert ranked[0].net_profit == 200  # higher net wins the tie


# ------------------------- cross-chain dedup regression -------------------- #
# The dedup key used to be built from bare pool-address strings with no chain
# tag. Several of this engine's shipped chains are OP-Stack siblings that
# legitimately share identical predeploy addresses (root CLAUDE.md §12 finding
# I2 hit the exact same shape of bug in ingestion's own pool-verification set).
# So a cross-chain opportunity's remote (other-chain) leg could coincide, in bare
# address, with an unrelated same-chain opportunity's pool -- silently dropping
# one of two genuinely distinct, real opportunities as a "duplicate" of the
# other. Blockstamp reuse below is deliberate: dedup never inspects it.
CHAIN_B = 8453


def test_cross_chain_leg_address_is_not_confused_with_an_unrelated_same_chain_pool() -> None:
    shared_addr = "0x" + "ab" * 20  # e.g. a predeploy-style address shared across chains
    remote_token = Token(chain_id=CHAIN_B, address="0x" + "33" * 20, decimals=18, symbol="C")
    local_token = Token(chain_id=CHAIN, address="0x" + "44" * 20, decimals=18, symbol="D")

    cross_chain = Opportunity(
        strategy=StrategyKind.CROSS_CHAIN_TWO_HOP,
        numeraire=A,
        input_amount=1000,
        output_amount=1200,
        gross_profit=200,
        gas_cost=0,
        net_profit=200,
        profit_bps=200.0,
        legs=(
            Leg(shared_addr, A, B, 1000, 500),  # on CHAIN (buy leg)
            Leg("p2", remote_token, A, 500, 1200),  # on CHAIN_B (sell leg)
        ),
        blockstamp=BS,
        chain_ids=(CHAIN, CHAIN_B),
        risk=RiskAssessment(0.9, 0.7, 0.1),
        score=140.0,
    )
    same_chain = Opportunity(
        strategy=StrategyKind.TWO_HOP,
        numeraire=A,
        input_amount=5000,
        output_amount=5900,
        gross_profit=900,
        gas_cost=0,
        net_profit=900,
        profit_bps=900.0,
        legs=(
            Leg(shared_addr, A, local_token, 5000, 3000),  # same address, same chain (CHAIN)
            # Same literal address string as cross_chain's CHAIN_B leg ("p2"),
            # but THIS leg's token genuinely lives on CHAIN -- an unrelated,
            # real same-chain pool that merely happens to share the string.
            Leg("p2", local_token, A, 3000, 5900),
        ),
        blockstamp=BS,
        chain_ids=(CHAIN,),
        risk=RiskAssessment(0.9, 0.7, 0.1),
        score=900.0,
    )

    ranked = rank_opportunities([cross_chain, same_chain], top_n=10)
    # Both are real, distinct opportunities on different chain combinations --
    # neither may be silently dropped as a "duplicate" of the other.
    assert len(ranked) == 2
    assert {o.strategy for o in ranked} == {
        StrategyKind.CROSS_CHAIN_TWO_HOP,
        StrategyKind.TWO_HOP,
    }
