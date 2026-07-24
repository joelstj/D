"""Token — a canonicalized ERC-20 identity on a specific chain.

The identity of a token is the pair ``(chain_id, address)``, with the address
lower-cased so it is a canonical graph-node key. ``decimals`` is **read on-chain**
per token and carried here because unit confusion (assuming 18 decimals) is the
#1 on-chain pricing bug (learnings.md: ``[amm/units]``). USDC has 6, WBTC has 8,
WETH has 18 — never assume.

``symbol`` is informational only and is *never* used for identity or fungibility
decisions (two different tokens can share a symbol; cross-chain "USDC" is not one
asset — see :mod:`l2arb.model.canonical_asset`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from l2arb.errors import DataError

__all__ = ["Token", "TokenKey"]

_ADDRESS_RE = re.compile(r"^0x[0-9a-f]{40}$")

# A token's globally-unique key: (chain_id, lower-cased address).
TokenKey = tuple[int, str]


@dataclass(frozen=True, slots=True)
class Token:
    """An ERC-20 token identity + its on-chain ``decimals``.

    Frozen + slotted: tokens are hashable value objects used directly as graph
    node keys via :attr:`key`.
    """

    chain_id: int
    address: str
    decimals: int
    symbol: str = ""
    # Tokens with transfer hooks (fee-on-transfer / rebasing) break the
    # constant-product assumption and must be kept out of the graph (SECURITY §3,
    # learnings.md ``[tokens]``). Quarantined tokens are still representable so
    # they can be *excluded* explicitly rather than silently mispriced.
    quarantined: bool = field(default=False)

    def __post_init__(self) -> None:
        if self.chain_id <= 0:
            raise DataError(f"chain_id must be positive, got {self.chain_id}")
        if not 0 <= self.decimals <= 36:
            raise DataError(f"decimals out of range [0, 36], got {self.decimals}")
        normalised = self.address.lower()
        if not _ADDRESS_RE.match(normalised):
            raise DataError(f"address must be 0x + 40 hex chars, got {self.address!r}")
        if normalised != self.address:
            object.__setattr__(self, "address", normalised)

    @property
    def key(self) -> TokenKey:
        """Canonical, hashable node key: ``(chain_id, address)``."""
        return (self.chain_id, self.address)

    @property
    def scale(self) -> int:
        """``10 ** decimals`` — the divisor from base units to whole tokens."""
        # decimals is validated non-negative, so this is always an int
        # (int ** int is typed ``Any`` because negative exponents yield float).
        return int(10**self.decimals)

    def to_whole(self, base_units: int) -> float:
        """Convert an integer base-unit amount to a whole-token float.

        For **reporting only** — never feed the result back into exact AMM math,
        which operates entirely in integer base units.
        """
        return base_units / self.scale
