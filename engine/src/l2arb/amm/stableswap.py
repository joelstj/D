"""Exact StableSwap (Curve) swap math for 2-coin pools.

Curve's invariant for ``n`` coins with amplification ``A`` (whitepaper §3):

    ``A·n^n·Σx_i + D = A·D·n^n + D^(n+1) / (n^n · Π x_i)``

``D`` is solved by Newton iteration; the output balance ``y`` for a given input is
solved by a second Newton iteration. This module implements the widely-deployed
Curve reference exactly (integer arithmetic, the ``-1`` output rounding), so a
local quote matches the pool for a 2-coin swap (the ``chain`` tier cross-checks a
live pool). Amounts are integer base units; for a mixed-decimals pair the caller
normalises balances to a common precision, as Curve does with its ``rates``.

Stable pools have very low slippage near balance — most stablecoin arbitrage lives
here — so this materially widens the engine's cross-dex coverage.
"""

from __future__ import annotations

from l2arb.constants import FEE_DENOMINATOR

__all__ = ["amount_out", "get_d", "get_y", "marginal_rate"]

N_COINS = 2


def _validate(balance_in: int, balance_out: int, amp: int) -> None:
    if balance_in <= 0 or balance_out <= 0:
        raise ValueError(f"balances must be positive, got in={balance_in} out={balance_out}")
    if amp <= 0:
        raise ValueError(f"amplification must be positive, got {amp}")


def get_d(balances: tuple[int, int], amp: int) -> int:
    """The StableSwap invariant ``D`` for ``balances`` at amplification ``amp``.

    Newton iteration matching Curve's ``get_D`` (``Ann = amp · N_COINS``).
    """
    total = balances[0] + balances[1]
    if total == 0:
        return 0
    d = total
    ann = amp * N_COINS
    for _ in range(255):  # pragma: no branch - Newton always converges (breaks)
        d_p = d
        for x in balances:
            d_p = d_p * d // (x * N_COINS)
        d_prev = d
        d = (ann * total + d_p * N_COINS) * d // ((ann - 1) * d + (N_COINS + 1) * d_p)
        if -1 <= d - d_prev <= 1:
            break
    return d


def get_y(new_balance_in: int, amp: int, d: int) -> int:
    """Output-coin balance ``y`` given the new input-coin balance and invariant ``D``.

    Matches Curve's ``get_y`` specialised to 2 coins.
    """
    ann = amp * N_COINS
    c = d * d // (new_balance_in * N_COINS)
    c = c * d // (ann * N_COINS)
    b = new_balance_in + d // ann
    y = d
    for _ in range(255):  # pragma: no branch - Newton always converges (breaks)
        y_prev = y
        y = (y * y + c) // (2 * y + b - d)
        if -1 <= y - y_prev <= 1:
            break
    return y


def amount_out(balance_in: int, balance_out: int, amp: int, amount_in: int, fee_pips: int) -> int:
    """Output amount for an exact input on a 2-coin StableSwap pool.

    ``dy = (balance_out - y - 1)`` less the fee, exactly as Curve computes it.
    Returns 0 for a zero input.
    """
    _validate(balance_in, balance_out, amp)
    if not 0 <= fee_pips < FEE_DENOMINATOR:
        raise ValueError(f"fee_pips out of range [0, {FEE_DENOMINATOR}): {fee_pips}")
    if amount_in < 0:
        raise ValueError(f"amount_in must be non-negative, got {amount_in}")
    if amount_in == 0:
        return 0
    d = get_d((balance_in, balance_out), amp)
    y = get_y(balance_in + amount_in, amp, d)
    dy = balance_out - y - 1
    if dy <= 0:
        return 0
    fee = dy * fee_pips // FEE_DENOMINATOR
    return dy - fee


def marginal_rate(balance_in: int, balance_out: int, amp: int, fee_pips: int) -> float:
    """Fee-inclusive marginal rate (out base units per in base unit).

    Estimated from a small probe swap (StableSwap has no simple closed-form spot
    rate). This is a graph-edge candidate signal only; exact profit is re-checked
    with :func:`amount_out` at the optimal size (ADR-005).
    """
    _validate(balance_in, balance_out, amp)
    probe = max(1, balance_in // 1_000)
    out = amount_out(balance_in, balance_out, amp, probe, fee_pips)
    return out / probe
