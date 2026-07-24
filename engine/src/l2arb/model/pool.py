"""Pool state snapshots — the decoded, block-stamped state of a single DEX pool.

Two shapes are supported (the two AMM families that dominate L2 liquidity):

* :class:`V2Reserves` — constant-product (Uniswap V2, Sushi, Aerodrome vAMM,
  Camelot v2, PancakeSwap v2, …).
* :class:`V3Slot0` — concentrated liquidity (Uniswap V3, PancakeSwap v3,
  Aerodrome CL, …).

A :class:`PoolState` bundles the pool identity, its ordered token pair, the fee,
the family-specific state, and — non-negotiably — the :class:`Blockstamp` it was
read at plus a ``verified`` flag (CLAUDE.md §3). It is **pure data**: it holds no
pricing logic. Pricing lives in :mod:`l2arb.amm` so the same exact math serves
both the graph edges and the exact re-pricing step.

Fee convention (unified across families): ``fee_pips`` is the fee in
**millionths** — ``fee = fee_pips / 1_000_000``. So 0.30 % ⇒ 3000, 0.05 % ⇒ 500,
0.25 % ⇒ 2500. This reproduces the classic V2 ``997/1000`` ratio exactly
(``(1_000_000 - 3000) / 1_000_000 = 997/1000``) and matches V3 fee-tier units
(which are already millionths) directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from l2arb.constants import (
    FEE_DENOMINATOR,
    MAX_SQRT_RATIO,
    MAX_TICK,
    MIN_SQRT_RATIO,
    MIN_TICK,
)
from l2arb.errors import PoolStateError
from l2arb.model.blockstamp import Blockstamp
from l2arb.model.token import Token, TokenKey

__all__ = [
    "FEE_DENOMINATOR",
    "MAX_SQRT_RATIO",
    "MAX_TICK",
    "MIN_SQRT_RATIO",
    "MIN_TICK",
    "PoolKind",
    "PoolState",
    "StableSwapState",
    "V2Reserves",
    "V3Slot0",
    "WeightedState",
]


class PoolKind(Enum):
    """The AMM family of a pool. Selects which exact math prices it."""

    CONSTANT_PRODUCT = "v2"
    CONCENTRATED_LIQUIDITY = "v3"
    STABLESWAP = "stable"
    WEIGHTED = "weighted"


@dataclass(frozen=True, slots=True)
class V2Reserves:
    """Constant-product reserves for the pool's ordered pair ``(token0, token1)``.

    Amounts are integers in each token's smallest unit (base units / wei-like).
    """

    reserve0: int
    reserve1: int

    def __post_init__(self) -> None:
        if self.reserve0 < 0 or self.reserve1 < 0:
            raise PoolStateError(f"reserves must be non-negative, got {self!r}")

    @property
    def tradable(self) -> bool:
        """A pool with a zero reserve on either side cannot be traded."""
        return self.reserve0 > 0 and self.reserve1 > 0


@dataclass(frozen=True, slots=True)
class V3Slot0:
    """Concentrated-liquidity state: ``sqrtPriceX96``, current ``tick``, ``L``.

    ``sqrt_price_x96`` is the Q64.96 fixed-point square-root of the price of
    token0 in token1 (``price = (sqrtPriceX96 / 2**96) ** 2``). ``liquidity`` is
    the currently-active in-range liquidity ``L`` (an integer). ``tick`` is the
    current tick.
    """

    sqrt_price_x96: int
    tick: int
    liquidity: int

    def __post_init__(self) -> None:
        if not MIN_SQRT_RATIO <= self.sqrt_price_x96 <= MAX_SQRT_RATIO:
            raise PoolStateError(f"sqrtPriceX96 out of range: {self.sqrt_price_x96}")
        if not MIN_TICK <= self.tick <= MAX_TICK:
            raise PoolStateError(f"tick out of range [{MIN_TICK}, {MAX_TICK}]: {self.tick}")
        if self.liquidity < 0:
            raise PoolStateError(f"liquidity must be non-negative, got {self.liquidity}")

    @property
    def tradable(self) -> bool:
        """No active liquidity ⇒ no swap can execute in-range."""
        return self.liquidity > 0


@dataclass(frozen=True, slots=True)
class StableSwapState:
    """Curve StableSwap 2-coin state: balances + amplification ``A``.

    Balances are in each token's base units (for a same-decimals stable pair the
    caller passes them directly; for mixed decimals the caller normalises to a
    common precision, as Curve does with its ``rates``). ``amp`` is Curve's
    amplification coefficient ``A`` (higher ``A`` ⇒ flatter, lower-slippage curve).
    """

    balance0: int
    balance1: int
    amp: int

    def __post_init__(self) -> None:
        if self.balance0 < 0 or self.balance1 < 0:
            raise PoolStateError(f"stableswap balances must be non-negative, got {self!r}")
        if self.amp <= 0:
            raise PoolStateError(f"amplification must be positive, got {self.amp}")

    @property
    def tradable(self) -> bool:
        return self.balance0 > 0 and self.balance1 > 0


@dataclass(frozen=True, slots=True)
class WeightedState:
    """Balancer weighted 2-token state: balances + normalised weights.

    Weights are fixed-point out of :data:`~l2arb.constants.WEIGHT_UNIT` (an 80/20
    pool is ``(8e17, 2e17)``); only the ratio ``w_in / w_out`` is used in pricing,
    so any consistent unit works. Balances are in each token's base units.
    """

    balance0: int
    balance1: int
    weight0: int
    weight1: int

    def __post_init__(self) -> None:
        if self.balance0 < 0 or self.balance1 < 0:
            raise PoolStateError(f"weighted balances must be non-negative, got {self!r}")
        if self.weight0 <= 0 or self.weight1 <= 0:
            raise PoolStateError(f"weights must be positive, got ({self.weight0}, {self.weight1})")

    @property
    def tradable(self) -> bool:
        return self.balance0 > 0 and self.balance1 > 0


@dataclass(frozen=True, slots=True)
class PoolState:
    """A block-stamped snapshot of one pool. Pure data; pricing lives in ``amm``.

    Exactly one family-specific state (:attr:`v2` / :attr:`v3` / :attr:`stable` /
    :attr:`weighted`) is populated, matching :attr:`kind`.
    """

    address: str
    kind: PoolKind
    token0: Token
    token1: Token
    fee_pips: int
    blockstamp: Blockstamp
    v2: V2Reserves | None = None
    v3: V3Slot0 | None = None
    stable: StableSwapState | None = None
    weighted: WeightedState | None = None
    verified: bool = False

    def __post_init__(self) -> None:
        if self.token0.key == self.token1.key:
            raise PoolStateError("token0 and token1 must differ")
        if self.token0.chain_id != self.token1.chain_id:
            raise PoolStateError("pool tokens must be on the same chain")
        if self.token0.chain_id != self.blockstamp.chain_id:
            raise PoolStateError("blockstamp chain must match the pool's tokens")
        if not 0 <= self.fee_pips < FEE_DENOMINATOR:
            raise PoolStateError(f"fee_pips out of range [0, {FEE_DENOMINATOR}): {self.fee_pips}")
        # Exactly one family state must be present, and it must match the kind.
        present = [kind for kind, state in self._states.items() if state is not None]
        if present != [self.kind]:
            raise PoolStateError(
                f"{self.kind.value} pool must carry exactly its own state, got {present}"
            )

    @property
    def _states(self) -> dict[PoolKind, object]:
        return {
            PoolKind.CONSTANT_PRODUCT: self.v2,
            PoolKind.CONCENTRATED_LIQUIDITY: self.v3,
            PoolKind.STABLESWAP: self.stable,
            PoolKind.WEIGHTED: self.weighted,
        }

    @property
    def chain_id(self) -> int:
        return self.token0.chain_id

    @property
    def token_keys(self) -> tuple[TokenKey, TokenKey]:
        return (self.token0.key, self.token1.key)

    @property
    def tradable(self) -> bool:
        """Whether the pool currently has liquidity on both sides."""
        state = self._states[self.kind]
        return getattr(state, "tradable", False)

    def contains(self, token_key: TokenKey) -> bool:
        return token_key in self.token_keys

    def other(self, token_key: TokenKey) -> Token:
        """Return the *other* token given one side of the pair."""
        if token_key == self.token0.key:
            return self.token1
        if token_key == self.token1.key:
            return self.token0
        raise PoolStateError(f"token {token_key} is not in pool {self.address}")

    def is_token0_input(self, token_in_key: TokenKey) -> bool:
        """True iff ``token_in_key`` is ``token0`` (i.e. swap direction 0→1)."""
        if token_in_key == self.token0.key:
            return True
        if token_in_key == self.token1.key:
            return False
        raise PoolStateError(f"token {token_in_key} is not in pool {self.address}")

    def oriented_v2_reserves(self, token_in_key: TokenKey) -> tuple[int, int]:
        """Return ``(reserve_in, reserve_out)`` for a V2 swap of ``token_in``.

        Raises if the pool is not a constant-product pool — callers dispatch on
        :attr:`kind` first.
        """
        if self.kind is not PoolKind.CONSTANT_PRODUCT or self.v2 is None:
            raise PoolStateError("oriented_v2_reserves is only valid for V2 pools")
        if self.is_token0_input(token_in_key):
            return (self.v2.reserve0, self.v2.reserve1)
        return (self.v2.reserve1, self.v2.reserve0)

    def oriented_stable(self, token_in_key: TokenKey) -> tuple[int, int, int]:
        """Return ``(balance_in, balance_out, amp)`` for a StableSwap swap."""
        if self.kind is not PoolKind.STABLESWAP or self.stable is None:
            raise PoolStateError("oriented_stable is only valid for StableSwap pools")
        if self.is_token0_input(token_in_key):
            return (self.stable.balance0, self.stable.balance1, self.stable.amp)
        return (self.stable.balance1, self.stable.balance0, self.stable.amp)

    def oriented_weighted(self, token_in_key: TokenKey) -> tuple[int, int, int, int]:
        """Return ``(balance_in, balance_out, weight_in, weight_out)`` for a Weighted swap."""
        if self.kind is not PoolKind.WEIGHTED or self.weighted is None:
            raise PoolStateError("oriented_weighted is only valid for Weighted pools")
        w = self.weighted
        if self.is_token0_input(token_in_key):
            return (w.balance0, w.balance1, w.weight0, w.weight1)
        return (w.balance1, w.balance0, w.weight1, w.weight0)
