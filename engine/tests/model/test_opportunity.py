"""Unit tests for the Opportunity value objects."""

from __future__ import annotations

import pytest

from l2arb.model.blockstamp import Blockstamp
from l2arb.model.opportunity import Leg, Opportunity, RiskAssessment, StrategyKind
from l2arb.model.token import Token

pytestmark = pytest.mark.unit

CHAIN = 42161
BS = Blockstamp(chain_id=CHAIN, number=100, block_hash="0x" + "ab" * 32, timestamp=1)
A = Token(chain_id=CHAIN, address="0x" + "11" * 20, decimals=18, symbol="A")
B = Token(chain_id=CHAIN, address="0x" + "22" * 20, decimals=6, symbol="B")


def _leg(pool: str, ti: Token, to: Token) -> Leg:
    return Leg(pool_address=pool, token_in=ti, token_out=to, amount_in=100, amount_out=110)


def _opp(**over: object) -> Opportunity:
    kw: dict[str, object] = {
        "strategy": StrategyKind.TWO_HOP,
        "numeraire": A,
        "input_amount": 1000,
        "output_amount": 1100,
        "gross_profit": 100,
        "gas_cost": 10,
        "net_profit": 90,
        "profit_bps": 900.0,
        "legs": (_leg("0xp1", A, B), _leg("0xp2", B, A)),
        "blockstamp": BS,
        "chain_ids": (CHAIN,),
        "risk": RiskAssessment(0.9, 0.7, 0.1, ("note",)),
        "score": 56.0,
    }
    kw.update(over)
    return Opportunity(**kw)  # type: ignore[arg-type]


def test_derived_properties() -> None:
    opp = _opp()
    assert opp.hops == 2
    assert opp.is_cross_chain is False
    assert opp.pool_addresses == ("0xp1", "0xp2")


def test_cross_chain_flag() -> None:
    opp = _opp(chain_ids=(42161, 8453))
    assert opp.is_cross_chain is True


def test_is_frozen() -> None:
    opp = _opp()
    with pytest.raises(Exception):  # noqa: B017,PT011 - dataclass FrozenInstanceError
        opp.net_profit = 5  # type: ignore[misc]


def test_strategy_kinds_are_distinct() -> None:
    assert len({k.value for k in StrategyKind}) == len(list(StrategyKind))
