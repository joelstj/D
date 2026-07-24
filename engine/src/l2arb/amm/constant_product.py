"""Exact constant-product (Uniswap V2 family) swap math.

Reproduces the on-chain arithmetic **bit-for-bit** so a locally-computed quote
equals what the contract would return (learnings.md ``[amm/v2]``). Every function
here is pure and operates entirely in Python ``int`` base units — no float in the
executable path, no fixed-width integers (reserves reach 2**112).

Reference: Uniswap V2 ``UniswapV2Library.getAmountOut`` / ``getAmountIn`` and the
whitepaper. Fee is supplied as ``fee_pips`` (millionths); the classic 0.30 % pool
is ``fee_pips = 3000`` which makes ``(FEE_DENOMINATOR - fee_pips)/FEE_DENOMINATOR
= 997/1000`` — identical to the contract's ratio, so results match exactly.

All amounts are integers in the token's smallest unit.
"""

from __future__ import annotations

from l2arb.constants import FEE_DENOMINATOR

__all__ = [
    "amount_in_for_out",
    "amount_out_for_in",
    "amount_out_unchecked",
    "marginal_rate",
    "price_impact_bps",
    "swap_exact_in",
]


def _validate_reserves(reserve_in: int, reserve_out: int) -> None:
    if reserve_in <= 0 or reserve_out <= 0:
        raise ValueError(f"reserves must be positive, got in={reserve_in} out={reserve_out}")


def _validate_fee(fee_pips: int) -> None:
    if not 0 <= fee_pips < FEE_DENOMINATOR:
        raise ValueError(f"fee_pips out of range [0, {FEE_DENOMINATOR}): {fee_pips}")


def amount_out_unchecked(reserve_in: int, reserve_out: int, amount_in: int, fee_pips: int) -> int:
    """``amount_out_for_in`` without input validation — the hot-path fast core.

    The exact same arithmetic; callers that have already validated their reserves
    and fee (e.g. a :class:`PoolState`, validated at construction) use this in the
    size-search inner loop to skip re-validating fixed values on every evaluation.
    Non-positive ``amount_in`` yields 0.
    """
    if amount_in <= 0:
        return 0
    amount_in_with_fee = amount_in * (FEE_DENOMINATOR - fee_pips)
    numerator = amount_in_with_fee * reserve_out
    denominator = reserve_in * FEE_DENOMINATOR + amount_in_with_fee
    return numerator // denominator


def amount_out_for_in(reserve_in: int, reserve_out: int, amount_in: int, fee_pips: int) -> int:
    """Output amount for an exact input (``getAmountOut``).

    ``dy = (reserve_out * dx_eff) // (reserve_in * FEE_DEN + dx_eff)`` where
    ``dx_eff = amount_in * (FEE_DEN - fee_pips)``. Floor division matches the EVM.

    Returns 0 for a zero input. Raises on non-positive reserves or an input that
    is negative.
    """
    _validate_reserves(reserve_in, reserve_out)
    _validate_fee(fee_pips)
    if amount_in < 0:
        raise ValueError(f"amount_in must be non-negative, got {amount_in}")
    return amount_out_unchecked(reserve_in, reserve_out, amount_in, fee_pips)


def amount_in_for_out(reserve_in: int, reserve_out: int, amount_out: int, fee_pips: int) -> int:
    """Minimum input required to receive an exact output (``getAmountIn``).

    ``dx = (reserve_in * dy * FEE_DEN) // ((reserve_out - dy) * (FEE_DEN - fee)) + 1``.
    The trailing ``+ 1`` mirrors the contract: it guarantees the pool receives
    *enough* input (rounding always favours the pool), so it can exceed the
    mathematical ceiling by one unit when the division is exact — intentional, to
    match on-chain sufficiency.

    Raises if ``amount_out`` is not strictly less than ``reserve_out`` (a pool can
    never output its entire reserve).
    """
    _validate_reserves(reserve_in, reserve_out)
    _validate_fee(fee_pips)
    if amount_out < 0:
        raise ValueError(f"amount_out must be non-negative, got {amount_out}")
    if amount_out == 0:
        return 0
    if amount_out >= reserve_out:
        raise ValueError(f"amount_out {amount_out} >= reserve_out {reserve_out}: undrainable")
    numerator = reserve_in * amount_out * FEE_DENOMINATOR
    denominator = (reserve_out - amount_out) * (FEE_DENOMINATOR - fee_pips)
    return numerator // denominator + 1


def swap_exact_in(
    reserve_in: int, reserve_out: int, amount_in: int, fee_pips: int
) -> tuple[int, int, int]:
    """Return ``(amount_out, new_reserve_in, new_reserve_out)`` after the swap.

    Post-trade reserves let callers chain swaps through a multi-hop cycle,
    including the rare case where the same pool appears twice. The constant-
    product invariant weakly increases: ``new_in * new_out >= reserve_in *
    reserve_out`` (the fee accrues to LPs) — pinned by a property test.
    """
    amount_out = amount_out_for_in(reserve_in, reserve_out, amount_in, fee_pips)
    return amount_out, reserve_in + amount_in, reserve_out - amount_out


def marginal_rate(reserve_in: int, reserve_out: int, fee_pips: int) -> float:
    """Executable marginal (infinitesimal) rate of ``in`` in ``out``, fee-inclusive.

    ``r = (reserve_out / reserve_in) * (FEE_DEN - fee_pips) / FEE_DEN``. This is
    the graph-edge rate: a **float** used only for candidate generation. Exact
    profitability is always re-checked with the integer math above (ADR-005).
    """
    _validate_reserves(reserve_in, reserve_out)
    _validate_fee(fee_pips)
    fee_factor = (FEE_DENOMINATOR - fee_pips) / FEE_DENOMINATOR
    return (reserve_out / reserve_in) * fee_factor


def price_impact_bps(reserve_in: int, reserve_out: int, amount_in: int, fee_pips: int) -> float:
    """Size-induced price impact of a swap, in basis points (excludes the fee).

    Compares the swap's effective rate ``dy/dx`` against the fee-inclusive
    marginal rate so the number isolates slippage from the fee. Range ``[0, 1e4)``
    bps; grows with trade size relative to reserves. Returns 0.0 for a zero input.
    """
    if amount_in <= 0:
        return 0.0
    amount_out = amount_out_for_in(reserve_in, reserve_out, amount_in, fee_pips)
    spot = marginal_rate(reserve_in, reserve_out, fee_pips)
    effective = amount_out / amount_in
    impact = 1.0 - (effective / spot)
    return max(0.0, impact) * 10_000.0
