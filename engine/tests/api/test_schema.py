"""Unit tests for the API schema (request validation + opportunity output)."""

from __future__ import annotations

import pytest

from l2arb.api.schema import DetectRequest, opportunity_to_dict, token_to_output
from l2arb.model.blockstamp import Blockstamp
from l2arb.model.opportunity import Leg, Opportunity, RiskAssessment, StrategyKind
from l2arb.model.token import Token

pytestmark = pytest.mark.unit

CHAIN = 42161
BS = Blockstamp(chain_id=CHAIN, number=100, block_hash="0x" + "ab" * 32, timestamp=7)
A = Token(chain_id=CHAIN, address="0x" + "11" * 20, decimals=18, symbol="A")
B = Token(chain_id=CHAIN, address="0x" + "22" * 20, decimals=6, symbol="B")


def test_request_defaults() -> None:
    req = DetectRequest()
    assert req.top_n == 10
    assert req.max_hops == 4
    assert req.chains == []


def test_request_rejects_out_of_range_max_hops() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        DetectRequest(max_hops=1)
    with pytest.raises(ValidationError):
        DetectRequest(max_hops=99)


def test_token_to_output() -> None:
    out = token_to_output(A)
    assert out == {"chain_id": CHAIN, "address": A.address, "decimals": 18, "symbol": "A"}


def test_opportunity_to_dict_shape() -> None:
    opp = Opportunity(
        strategy=StrategyKind.CROSS_CHAIN_TWO_HOP,
        numeraire=A,
        input_amount=10**21,
        output_amount=10**21 + 500,
        gross_profit=800,
        gas_cost=100,
        net_profit=500,
        profit_bps=5.0,
        legs=(Leg("0xp1", A, B, 10**21, 3 * 10**9), Leg("0xp2", B, A, 3 * 10**9, 10**21 + 500)),
        blockstamp=BS,
        chain_ids=(8453, 42161),
        risk=RiskAssessment(0.7, 0.6, 0.3, ("cross_chain=True",)),
        score=210.0,
        verified=True,
        expected_net=210,
        bridge_cost=200,
        settle_seconds=600,
    )
    data = opportunity_to_dict(opp)
    assert data["strategy"] == "cross_chain_two_hop"
    assert data["input_amount"] == str(10**21)  # big int as string
    assert data["is_cross_chain"] is True
    assert data["settle_seconds"] == 600
    assert data["bridge_cost"] == "200"
    assert data["chain_ids"] == [8453, 42161]
    assert data["block"]["number"] == 100
    assert data["risk"]["success_probability"] == 0.7
    assert len(data["legs"]) == 2
    assert data["legs"][0]["amount_in"] == str(10**21)
    assert data["legs"][0]["token_out"]["symbol"] == "B"
