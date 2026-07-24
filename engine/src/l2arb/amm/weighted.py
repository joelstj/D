"""Exact-enough weighted-pool (Balancer) swap math for 2-token pools.

Balancer's weighted out-given-in (whitepaper §3, ``WeightedMath``):

    ``amountOut = balanceOut · (1 - (balanceIn / (balanceIn + amountInAfterFee)) ^ (wIn/wOut))``

The fractional exponent ``wIn/wOut`` needs a real power, which the on-chain code
does with fixed-point ``LogExpMath``. We compute it with :class:`decimal.Decimal`
at high precision and **floor** the result, so the quote is accurate to far below
one wei and is **never overstated** — flooring only ever under-reports, the safe
direction for a detector (the ``chain`` tier cross-checks a live pool). The spot
(marginal) rate is closed-form and exact.

Weights are fixed-point (see :data:`~l2arb.constants.WEIGHT_UNIT`); only the ratio
``wIn/wOut`` matters. Amounts are integer base units.
"""

from __future__ import annotations

from decimal import Decimal, localcontext

from l2arb.constants import FEE_DENOMINATOR

__all__ = ["amount_out", "marginal_rate"]

_PRECISION = 60  # Decimal significant digits — far beyond wei resolution


def _validate(balance_in: int, balance_out: int, weight_in: int, weight_out: int) -> None:
    if balance_in <= 0 or balance_out <= 0:
        raise ValueError(f"balances must be positive, got in={balance_in} out={balance_out}")
    if weight_in <= 0 or weight_out <= 0:
        raise ValueError(f"weights must be positive, got in={weight_in} out={weight_out}")


def amount_out(
    balance_in: int,
    balance_out: int,
    weight_in: int,
    weight_out: int,
    amount_in: int,
    fee_pips: int,
) -> int:
    """Output amount for an exact input on a 2-token weighted pool (floored)."""
    _validate(balance_in, balance_out, weight_in, weight_out)
    if not 0 <= fee_pips < FEE_DENOMINATOR:
        raise ValueError(f"fee_pips out of range [0, {FEE_DENOMINATOR}): {fee_pips}")
    if amount_in < 0:
        raise ValueError(f"amount_in must be non-negative, got {amount_in}")
    if amount_in == 0:
        return 0
    amount_in_after_fee = amount_in * (FEE_DENOMINATOR - fee_pips) // FEE_DENOMINATOR
    if amount_in_after_fee == 0:
        return 0
    with localcontext() as ctx:
        ctx.prec = _PRECISION
        base = Decimal(balance_in) / (Decimal(balance_in) + Decimal(amount_in_after_fee))
        exponent = Decimal(weight_in) / Decimal(weight_out)
        # base in (0,1) -> ln < 0 -> power in (0,1); use ln/exp for a general power.
        power = (exponent * base.ln()).exp()
        out = Decimal(balance_out) * (Decimal(1) - power)
    # The true output is strictly < balance_out (a swap can never drain the pool). At
    # extreme weight ratios + huge inputs, `power` underflows the Decimal precision to
    # 0, which would round `out` up to exactly balance_out; clamp to balance_out - 1 so
    # we never overstate a full-drain that is physically impossible.
    return min(int(out), balance_out - 1)  # floor: never overstates the output


def marginal_rate(
    balance_in: int, balance_out: int, weight_in: int, weight_out: int, fee_pips: int
) -> float:
    """Fee-inclusive marginal rate (out base units per in base unit), closed-form.

    Balancer spot: ``(balanceOut/wOut) / (balanceIn/wIn) · (1 - fee)``.
    """
    _validate(balance_in, balance_out, weight_in, weight_out)
    fee_factor = (FEE_DENOMINATOR - fee_pips) / FEE_DENOMINATOR
    return (balance_out * weight_in) / (balance_in * weight_out) * fee_factor
