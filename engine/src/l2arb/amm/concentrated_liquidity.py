"""Exact concentrated-liquidity (Uniswap V3 family) swap math.

Implements the single-tick swap step faithfully to Uniswap V3 core
(``SqrtPriceMath`` / ``SwapMath``), including the exact rounding directions, so a
local quote matches the on-chain **QuoterV2** within 1 wei for swaps that stay
inside the active tick (learnings.md ``[amm/v3]``; the ``chain``/``verify`` tiers
assert this against a live quoter). All math is Python ``int`` in Q64.96 fixed
point — ``sqrtPriceX96`` reaches ~2**160, so fixed-width integers are unsafe.

**Crossing ticks safely.** Full multi-tick crossing needs the tick bitmap +
per-tick ``liquidityNet`` read on-chain. Rather than guess beyond the active
range, each swap accepts an optional ``sqrt_price_limit_x96`` (the next
initialized tick in the swap direction). If the swap would move price past it,
the step is **capped at the boundary**: the returned output is what executes up
to that price and is therefore a *lower bound* on the true output. Understating
output can only *suppress* an opportunity, never fabricate one — the safe
direction for a detector (docs/ARBITRAGE_THEORY §1.2).

**Caution — the unbounded default overstates across ticks.** When
``sqrt_price_limit_x96`` is ``None`` the step assumes the active ``liquidity``
holds all the way to the protocol price bound. That is exact *within* the current
tick but **overstates** output for a fill large enough to cross a tick into
different (usually thinner) liquidity. Callers that price real pools must pass the
active-range boundary so the quote stays a safe lower bound; the detection path
threads :attr:`~l2arb.model.pool.V3Slot0.sqrt_ratio_lower_x96` /
``sqrt_ratio_upper_x96`` for exactly this, and flags any opportunity still sized
on the unbounded estimate (``v3_single_tick_estimate``).

Direction convention (Uniswap): ``zeroForOne`` = input token0, output token1,
price (token1/token0) **decreases**. ``oneForZero`` = input token1, output
token0, price **increases**.
"""

from __future__ import annotations

from typing import NamedTuple

from l2arb.constants import FEE_DENOMINATOR, MAX_SQRT_RATIO, MIN_SQRT_RATIO, Q96, Q192

__all__ = [
    "SwapStep",
    "amount_out_0_for_1",
    "amount_out_1_for_0",
    "marginal_rate_0_for_1",
    "marginal_rate_1_for_0",
    "price0_in_1",
    "swap_0_for_1",
    "swap_1_for_0",
]


class SwapStep(NamedTuple):
    """Result of a single-tick swap step.

    ``amount_in_consumed`` may be less than the requested input when the step was
    capped at ``sqrt_price_limit_x96`` (``capped`` is then ``True``); the caller
    would need the next tick's liquidity to consume the remainder.
    """

    amount_in_consumed: int
    amount_out: int
    sqrt_price_next_x96: int
    capped: bool


def _validate(sqrt_price_x96: int, liquidity: int, fee_pips: int, amount_in: int) -> None:
    if not MIN_SQRT_RATIO <= sqrt_price_x96 <= MAX_SQRT_RATIO:
        raise ValueError(f"sqrtPriceX96 out of range: {sqrt_price_x96}")
    if liquidity <= 0:
        raise ValueError(f"liquidity must be positive, got {liquidity}")
    if not 0 <= fee_pips < FEE_DENOMINATOR:
        raise ValueError(f"fee_pips out of range [0, {FEE_DENOMINATOR}): {fee_pips}")
    if amount_in < 0:
        raise ValueError(f"amount_in must be non-negative, got {amount_in}")


def _amount0_delta(sqrt_lo: int, sqrt_hi: int, liquidity: int) -> int:
    """token0 between two sqrt prices (``sqrt_lo < sqrt_hi``), rounded **down**.

    ``amount0 = floor( floor(L * Q96 * (hi - lo) / hi) / lo )`` — the two-step
    floor matches ``SqrtPriceMath.getAmount0Delta`` roundDown exactly.
    """
    numerator1 = liquidity << 96
    numerator2 = sqrt_hi - sqrt_lo
    return (numerator1 * numerator2 // sqrt_hi) // sqrt_lo


def _amount1_delta(sqrt_lo: int, sqrt_hi: int, liquidity: int) -> int:
    """token1 between two sqrt prices, rounded **down**: ``L * (hi - lo) // Q96``."""
    return liquidity * (sqrt_hi - sqrt_lo) // Q96


def _next_sqrt_price_from_amount0(sqrt_price_x96: int, liquidity: int, amount0: int) -> int:
    """New sqrtPrice after adding ``amount0`` token0 (price falls), rounded **up**.

    ``sqrtP' = ceil( L*Q96*sqrtP / (L*Q96 + amount0*sqrtP) )`` — matches
    ``getNextSqrtPriceFromAmount0RoundingUp`` (add=true) with no overflow in
    Python ints.
    """
    if amount0 == 0:
        return sqrt_price_x96
    numerator1 = liquidity << 96
    product = amount0 * sqrt_price_x96
    denominator = numerator1 + product
    # mulDivRoundingUp(numerator1, sqrtP, denominator)
    return _mul_div_rounding_up(numerator1, sqrt_price_x96, denominator)


def _next_sqrt_price_from_amount1(sqrt_price_x96: int, liquidity: int, amount1: int) -> int:
    """New sqrtPrice after adding ``amount1`` token1 (price rises), rounded **down**.

    ``sqrtP' = sqrtP + floor(amount1 * Q96 / L)`` — matches
    ``getNextSqrtPriceFromAmount1RoundingDown`` (add=true).
    """
    return sqrt_price_x96 + (amount1 << 96) // liquidity


def _mul_div_rounding_up(a: int, b: int, denominator: int) -> int:
    """``ceil(a * b / denominator)`` for non-negative integers."""
    product = a * b
    result = product // denominator
    if product % denominator != 0:
        result += 1
    return result


def swap_0_for_1(
    sqrt_price_x96: int,
    liquidity: int,
    amount_in: int,
    fee_pips: int,
    sqrt_price_limit_x96: int | None = None,
) -> SwapStep:
    """Exact-input swap of token0 → token1 within the active tick (price falls).

    ``sqrt_price_limit_x96`` lower-bounds the price move (default: the protocol
    minimum, i.e. unbounded within the tick). Fee is taken on input first, exactly
    as ``SwapMath.computeSwapStep``.
    """
    _validate(sqrt_price_x96, liquidity, fee_pips, amount_in)
    # Price falls in 0->1; the limit is a lower bound, clamped into protocol range.
    limit = (
        MIN_SQRT_RATIO
        if sqrt_price_limit_x96 is None
        else max(sqrt_price_limit_x96, MIN_SQRT_RATIO)
    )
    if amount_in > 0 and limit >= sqrt_price_x96:
        # No room to move price downward — nothing can swap.
        return SwapStep(0, 0, sqrt_price_x96, capped=True)
    # A dust input whose post-fee remainder floors to 0 moves no price and yields
    # no output (the helper's amount0==0 guard returns the unchanged price).
    amount_in_less_fee = amount_in * (FEE_DENOMINATOR - fee_pips) // FEE_DENOMINATOR
    sqrt_next = _next_sqrt_price_from_amount0(sqrt_price_x96, liquidity, amount_in_less_fee)
    capped = sqrt_next < limit
    if capped:
        sqrt_next = limit
        # Input actually consumed to reach the boundary (round up), re-grossed for fee.
        consumed_less_fee = _amount0_in_to_reach(sqrt_next, sqrt_price_x96, liquidity)
        amount_in_consumed = min(amount_in, _gross_from_net(consumed_less_fee, fee_pips))
    else:
        amount_in_consumed = amount_in
    amount_out = _amount1_delta(sqrt_next, sqrt_price_x96, liquidity)
    return SwapStep(amount_in_consumed, amount_out, sqrt_next, capped)


def swap_1_for_0(
    sqrt_price_x96: int,
    liquidity: int,
    amount_in: int,
    fee_pips: int,
    sqrt_price_limit_x96: int | None = None,
) -> SwapStep:
    """Exact-input swap of token1 → token0 within the active tick (price rises)."""
    _validate(sqrt_price_x96, liquidity, fee_pips, amount_in)
    # Price rises in 1->0; the limit is an upper bound, clamped into protocol range.
    limit = (
        MAX_SQRT_RATIO
        if sqrt_price_limit_x96 is None
        else min(sqrt_price_limit_x96, MAX_SQRT_RATIO)
    )
    if amount_in > 0 and limit <= sqrt_price_x96:
        return SwapStep(0, 0, sqrt_price_x96, capped=True)
    amount_in_less_fee = amount_in * (FEE_DENOMINATOR - fee_pips) // FEE_DENOMINATOR
    sqrt_next = _next_sqrt_price_from_amount1(sqrt_price_x96, liquidity, amount_in_less_fee)
    capped = sqrt_next > limit
    if capped:
        sqrt_next = limit
        consumed_less_fee = _amount1_delta(sqrt_price_x96, sqrt_next, liquidity)
        amount_in_consumed = min(amount_in, _gross_from_net(consumed_less_fee, fee_pips))
    else:
        amount_in_consumed = amount_in
    amount_out = _amount0_delta(sqrt_price_x96, sqrt_next, liquidity)
    return SwapStep(amount_in_consumed, amount_out, sqrt_next, capped)


def _amount0_in_to_reach(sqrt_lo: int, sqrt_hi: int, liquidity: int) -> int:
    """token0 input (rounded up) to move price from ``sqrt_hi`` down to ``sqrt_lo``."""
    numerator1 = liquidity << 96
    numerator2 = sqrt_hi - sqrt_lo
    inner = _mul_div_rounding_up(numerator1, numerator2, sqrt_hi)
    return _mul_div_rounding_up(inner, 1, sqrt_lo)


def _gross_from_net(net_input: int, fee_pips: int) -> int:
    """Gross input whose post-fee remainder is at least ``net_input`` (round up)."""
    if fee_pips == 0:
        return net_input
    return _mul_div_rounding_up(net_input, FEE_DENOMINATOR, FEE_DENOMINATOR - fee_pips)


def amount_out_0_for_1(
    sqrt_price_x96: int,
    liquidity: int,
    amount_in: int,
    fee_pips: int,
    sqrt_price_limit_x96: int | None = None,
) -> int:
    """Convenience: token1 output for an exact token0 input (single tick)."""
    return swap_0_for_1(
        sqrt_price_x96, liquidity, amount_in, fee_pips, sqrt_price_limit_x96
    ).amount_out


def amount_out_1_for_0(
    sqrt_price_x96: int,
    liquidity: int,
    amount_in: int,
    fee_pips: int,
    sqrt_price_limit_x96: int | None = None,
) -> int:
    """Convenience: token0 output for an exact token1 input (single tick)."""
    return swap_1_for_0(
        sqrt_price_x96, liquidity, amount_in, fee_pips, sqrt_price_limit_x96
    ).amount_out


def price0_in_1(sqrt_price_x96: int) -> float:
    """Spot price of token0 denominated in token1, in **base units** (no fee).

    ``price = (sqrtPriceX96 / 2**96) ** 2``. Multiply by ``10**(dec0 - dec1)`` at
    the reporting edge for human units; base-unit rates are what the graph uses.
    """
    return (sqrt_price_x96 * sqrt_price_x96) / Q192


def marginal_rate_0_for_1(sqrt_price_x96: int, fee_pips: int) -> float:
    """Fee-inclusive marginal rate token0→token1 (out base units per in base unit)."""
    fee_factor = (FEE_DENOMINATOR - fee_pips) / FEE_DENOMINATOR
    return price0_in_1(sqrt_price_x96) * fee_factor


def marginal_rate_1_for_0(sqrt_price_x96: int, fee_pips: int) -> float:
    """Fee-inclusive marginal rate token1→token0 (out base units per in base unit)."""
    fee_factor = (FEE_DENOMINATOR - fee_pips) / FEE_DENOMINATOR
    return (1.0 / price0_in_1(sqrt_price_x96)) * fee_factor
