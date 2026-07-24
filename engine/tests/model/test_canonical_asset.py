"""Unit tests for the canonical-asset registry and fungibility rules."""

from __future__ import annotations

import pytest

from l2arb.errors import ConfigError
from l2arb.model.canonical_asset import AssetRegistry, AssetRepresentation, CanonicalAsset
from l2arb.model.token import Token

pytestmark = pytest.mark.unit

ARB, BASE = 42161, 8453
USDC_ARB = Token(chain_id=ARB, address="0x" + "a1" * 20, decimals=6, symbol="USDC")
USDC_BASE = Token(chain_id=BASE, address="0x" + "b1" * 20, decimals=6, symbol="USDC")
USDCE_ARB = Token(chain_id=ARB, address="0x" + "a2" * 20, decimals=6, symbol="USDC.e")


def _usdc() -> CanonicalAsset:
    return CanonicalAsset(
        "USDC",
        (
            AssetRepresentation(USDC_ARB, native=True, bridgeable=True),
            AssetRepresentation(USDC_BASE, native=True, bridgeable=True),
        ),
    )


def test_register_and_lookup() -> None:
    reg = AssetRegistry()
    reg.register(_usdc())
    assert reg.canonical_symbol(USDC_ARB.key) == "USDC"
    assert reg.asset("USDC") is not None
    assert reg.representation(USDC_ARB.key).chain_id == ARB  # type: ignore[union-attr]
    assert reg.symbols() == ("USDC",)


def test_native_usdc_is_fungible_across_chains() -> None:
    reg = AssetRegistry()
    reg.register(_usdc())
    assert reg.are_fungible(USDC_ARB.key, USDC_BASE.key) is True
    assert reg.are_fungible(USDC_ARB.key, USDC_ARB.key) is True  # identity


def test_bridged_variant_not_fungible_unless_flagged() -> None:
    reg = AssetRegistry()
    reg.register(
        CanonicalAsset(
            "USDC",
            (
                AssetRepresentation(USDC_ARB, native=True, bridgeable=True),
                AssetRepresentation(USDCE_ARB, native=False, bridgeable=False),  # USDC.e
            ),
        )
    )
    # Same canonical symbol, but the bridged variant is not bridgeable 1:1.
    assert reg.are_fungible(USDC_ARB.key, USDCE_ARB.key) is False


def test_unrelated_tokens_not_fungible() -> None:
    reg = AssetRegistry()
    reg.register(_usdc())
    weth = Token(chain_id=ARB, address="0x" + "c1" * 20, decimals=18, symbol="WETH")
    assert reg.are_fungible(USDC_ARB.key, weth.key) is False
    assert reg.canonical_symbol(weth.key) is None
    assert reg.representation(weth.key) is None


def test_duplicate_registration_rejected() -> None:
    reg = AssetRegistry()
    reg.register(_usdc())
    with pytest.raises(ConfigError, match="already registered"):
        reg.register(_usdc())


def test_token_mapped_twice_rejected() -> None:
    reg = AssetRegistry()
    reg.register(_usdc())
    with pytest.raises(ConfigError, match="already mapped"):
        reg.register(CanonicalAsset("USD", (AssetRepresentation(USDC_ARB),)))


def test_empty_symbol_and_duplicate_reps_rejected() -> None:
    with pytest.raises(ConfigError, match="non-empty symbol"):
        CanonicalAsset("", (AssetRepresentation(USDC_ARB),))
    with pytest.raises(ConfigError, match="duplicate representation"):
        CanonicalAsset("USDC", (AssetRepresentation(USDC_ARB), AssetRepresentation(USDC_ARB)))


def test_on_chain_and_bridgeable_helpers() -> None:
    asset = _usdc()
    assert asset.on_chain(ARB) is not None
    assert asset.on_chain(999) is None
    assert len(asset.bridgeable_representations()) == 2
