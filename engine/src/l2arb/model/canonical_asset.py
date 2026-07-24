"""Canonical assets — which (chain, address) tokens are the *same* asset.

Cross-chain arbitrage is only meaningful between tokens that are genuinely
fungible, and that is the hard part: "USDC" is not one asset (learnings.md
``[xchain]``). Native USDC and bridged USDC.e have different addresses **and
different risk**; WETH on Arbitrum is a different contract from WETH on Base. This
registry records, as curated but **on-chain-verifiable** config, which token
representations map to one canonical asset and whether each can be bridged 1:1
with negligible risk.

Fungibility for arbitrage is deliberately conservative: two representations are
treated as interchangeable only when they share a canonical id **and both are
flagged bridgeable**. A bridged variant that is not explicitly bridgeable is a
*distinct* asset — never assumed 1:1 (docs/ARBITRAGE_THEORY §5). The prices that
flow through this map are always live/on-chain; only the identity mapping is
curated, and it is meant to be verified against on-chain metadata.
"""

from __future__ import annotations

from dataclasses import dataclass

from l2arb.errors import ConfigError
from l2arb.model.token import Token, TokenKey

__all__ = ["AssetRegistry", "AssetRepresentation", "CanonicalAsset"]


@dataclass(frozen=True, slots=True)
class AssetRepresentation:
    """One on-chain representation of a canonical asset.

    ``native`` marks the canonical issuance (e.g. native USDC via CCTP);
    ``bridgeable`` marks that this representation can be moved to/from the other
    bridgeable representations 1:1 with acceptable risk. A non-bridgeable variant
    is representable (so it can be *excluded*), never silently treated as fungible.
    """

    token: Token
    native: bool = True
    bridgeable: bool = True

    @property
    def key(self) -> TokenKey:
        return self.token.key

    @property
    def chain_id(self) -> int:
        return self.token.chain_id


@dataclass(frozen=True, slots=True)
class CanonicalAsset:
    """A named asset class and its representations across chains."""

    symbol: str
    representations: tuple[AssetRepresentation, ...]

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ConfigError("canonical asset needs a non-empty symbol")
        keys = [r.key for r in self.representations]
        if len(set(keys)) != len(keys):
            raise ConfigError(f"duplicate representation in canonical asset {self.symbol}")

    def on_chain(self, chain_id: int) -> AssetRepresentation | None:
        return next((r for r in self.representations if r.chain_id == chain_id), None)

    def bridgeable_representations(self) -> tuple[AssetRepresentation, ...]:
        return tuple(r for r in self.representations if r.bridgeable)


class AssetRegistry:
    """Lookup from a token to its canonical asset, and fungibility checks."""

    def __init__(self) -> None:
        self._by_key: dict[TokenKey, str] = {}
        self._assets: dict[str, CanonicalAsset] = {}

    def register(self, asset: CanonicalAsset) -> None:
        """Register a canonical asset; a token may belong to only one asset."""
        if asset.symbol in self._assets:
            raise ConfigError(f"canonical asset {asset.symbol} already registered")
        for rep in asset.representations:
            if rep.key in self._by_key:
                raise ConfigError(f"token {rep.key} already mapped to {self._by_key[rep.key]}")
        self._assets[asset.symbol] = asset
        for rep in asset.representations:
            self._by_key[rep.key] = asset.symbol

    def canonical_symbol(self, token_key: TokenKey) -> str | None:
        return self._by_key.get(token_key)

    def asset(self, symbol: str) -> CanonicalAsset | None:
        return self._assets.get(symbol)

    def representation(self, token_key: TokenKey) -> AssetRepresentation | None:
        symbol = self._by_key.get(token_key)
        if symbol is None:
            return None
        return next(r for r in self._assets[symbol].representations if r.key == token_key)

    def are_fungible(self, key_a: TokenKey, key_b: TokenKey) -> bool:
        """True iff both tokens are the same canonical asset and both bridgeable.

        Same-key (identity) is trivially fungible. Cross-chain fungibility requires
        both representations to be explicitly bridgeable.
        """
        if key_a == key_b:
            return True
        symbol_a = self._by_key.get(key_a)
        if symbol_a is None or symbol_a != self._by_key.get(key_b):
            return False
        rep_a = self.representation(key_a)
        rep_b = self.representation(key_b)
        return bool(rep_a and rep_b and rep_a.bridgeable and rep_b.bridgeable)

    def symbols(self) -> tuple[str, ...]:
        return tuple(self._assets)
