"""Uniform pricing over :class:`PoolState`, dispatching to the exact AMM math.

This is the one place that maps a decoded pool + a swap direction to a number, so
the graph edges and the exact re-pricing step share identical math (DRY — no DEX
adapter re-implements pricing). Two operations:

* :func:`marginal_rate` — the fee-inclusive infinitesimal rate (out base units
  per in base unit) used as the graph edge weight input.
* :func:`amount_out` — the exact integer output for an exact input, used by the
  sizing solver and the final profit check.

Both take the **input token key** and orient the pool accordingly. Callers must
pass a :attr:`PoolState.tradable` pool; pricing an empty pool raises (fail loud).
"""

from __future__ import annotations

from typing import cast

from l2arb.amm import concentrated_liquidity as cl
from l2arb.amm import constant_product as cp
from l2arb.amm import stableswap as ss
from l2arb.amm import weighted as wp
from l2arb.errors import PoolStateError
from l2arb.model.pool import PoolKind, PoolState, V3Slot0
from l2arb.model.token import TokenKey

__all__ = ["amount_out", "marginal_rate"]


def marginal_rate(pool: PoolState, token_in_key: TokenKey) -> float:
    """Fee-inclusive marginal rate for swapping ``token_in`` through ``pool``.

    Units: output base units per input base unit. Around a cycle these compose to
    a dimensionless product, so no decimal scaling is needed for detection.
    """
    if not pool.contains(token_in_key):
        raise PoolStateError(f"token {token_in_key} not in pool {pool.address}")
    if pool.kind is PoolKind.CONSTANT_PRODUCT:
        reserve_in, reserve_out = pool.oriented_v2_reserves(token_in_key)
        return cp.marginal_rate(reserve_in, reserve_out, pool.fee_pips)
    if pool.kind is PoolKind.STABLESWAP:
        bal_in, bal_out, amp = pool.oriented_stable(token_in_key)
        return ss.marginal_rate(bal_in, bal_out, amp, pool.fee_pips)
    if pool.kind is PoolKind.WEIGHTED:
        bal_in, bal_out, w_in, w_out = pool.oriented_weighted(token_in_key)
        return wp.marginal_rate(bal_in, bal_out, w_in, w_out, pool.fee_pips)
    v3 = cast(V3Slot0, pool.v3)  # non-None for CONCENTRATED_LIQUIDITY (PoolState invariant)
    if pool.is_token0_input(token_in_key):
        return cl.marginal_rate_0_for_1(v3.sqrt_price_x96, pool.fee_pips)
    return cl.marginal_rate_1_for_0(v3.sqrt_price_x96, pool.fee_pips)


def amount_out(pool: PoolState, token_in_key: TokenKey, amount_in: int) -> int:
    """Exact integer output for swapping ``amount_in`` of ``token_in`` through ``pool``.

    For V3 this is the single-tick result. When the pool carries its active-range
    boundaries (:attr:`V3Slot0.sqrt_ratio_lower_x96` / ``…_upper_x96``) the fill is
    capped at the boundary, making the output a safe **lower bound** across ticks;
    without them it assumes constant liquidity within the tick and can overstate a
    tick-crossing fill (supply the boundaries for exact sizing). Weighted output is
    floored. This is the number the net-profit gate stands behind.
    """
    if not pool.contains(token_in_key):
        raise PoolStateError(f"token {token_in_key} not in pool {pool.address}")
    if pool.kind is PoolKind.CONSTANT_PRODUCT:
        reserve_in, reserve_out = pool.oriented_v2_reserves(token_in_key)
        return cp.amount_out_for_in(reserve_in, reserve_out, amount_in, pool.fee_pips)
    if pool.kind is PoolKind.STABLESWAP:
        bal_in, bal_out, amp = pool.oriented_stable(token_in_key)
        return ss.amount_out(bal_in, bal_out, amp, amount_in, pool.fee_pips)
    if pool.kind is PoolKind.WEIGHTED:
        bal_in, bal_out, w_in, w_out = pool.oriented_weighted(token_in_key)
        return wp.amount_out(bal_in, bal_out, w_in, w_out, amount_in, pool.fee_pips)
    v3 = cast(V3Slot0, pool.v3)  # non-None for CONCENTRATED_LIQUIDITY (PoolState invariant)
    if pool.is_token0_input(token_in_key):
        return cl.amount_out_0_for_1(
            v3.sqrt_price_x96, v3.liquidity, amount_in, pool.fee_pips, v3.sqrt_ratio_lower_x96
        )
    return cl.amount_out_1_for_0(
        v3.sqrt_price_x96, v3.liquidity, amount_in, pool.fee_pips, v3.sqrt_ratio_upper_x96
    )
