"""Unit tests for :class:`l2arb.model.token.Token`."""

from __future__ import annotations

import pytest

from l2arb.errors import DataError
from l2arb.model.token import Token

pytestmark = pytest.mark.unit

USDC = "0xaf88d065e77c8cc2239327c5edb3a432268e5831"  # Arbitrum native USDC (6 dp)


def test_construct_and_key() -> None:
    t = Token(chain_id=42161, address=USDC, decimals=6, symbol="USDC")
    assert t.key == (42161, USDC)
    assert t.scale == 10**6
    assert t.decimals == 6


def test_address_is_lowercased_for_identity() -> None:
    mixed = "0xAf88D065e77c8cC2239327C5EDb3A432268e5831"
    t = Token(chain_id=42161, address=mixed, decimals=6)
    assert t.address == USDC
    assert t.key == (42161, USDC)


def test_two_addresses_differing_only_in_case_are_equal() -> None:
    a = Token(chain_id=1, address=USDC.upper().replace("0X", "0x"), decimals=6)
    b = Token(chain_id=1, address=USDC, decimals=6)
    assert a == b
    assert hash(a) == hash(b)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("chain_id", 0),
        ("decimals", -1),
        ("decimals", 37),
        ("address", "0x1234"),
        ("address", "not-an-address"),
        ("address", "0x" + "zz" * 20),
    ],
)
def test_invalid_inputs_raise(field: str, value: object) -> None:
    kw: dict[str, object] = {"chain_id": 1, "address": USDC, "decimals": 18}
    kw[field] = value
    with pytest.raises(DataError):
        Token(**kw)  # type: ignore[arg-type]


def test_to_whole_reporting_helper() -> None:
    weth = Token(chain_id=1, address="0x" + "11" * 20, decimals=18)
    assert weth.to_whole(10**18) == pytest.approx(1.0)
    usdc = Token(chain_id=1, address=USDC, decimals=6)
    assert usdc.to_whole(2_500_000) == pytest.approx(2.5)


def test_quarantine_flag_defaults_false_and_is_representable() -> None:
    t = Token(chain_id=1, address=USDC, decimals=6)
    assert t.quarantined is False
    q = Token(chain_id=1, address=USDC, decimals=6, quarantined=True)
    assert q.quarantined is True
    # Same identity regardless of quarantine flag? No — dataclass eq includes it.
    assert t != q


def test_symbol_is_not_identity() -> None:
    a = Token(chain_id=1, address=USDC, decimals=6, symbol="USDC")
    b = Token(chain_id=1, address=USDC, decimals=6, symbol="USD Coin")
    # Symbols differ but they are the *same* token by key.
    assert a.key == b.key
