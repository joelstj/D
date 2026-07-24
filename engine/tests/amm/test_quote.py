"""Unit tests for the :mod:`l2arb.amm.quote` composition layer.

Verifies that quoting a :class:`PoolState` dispatches to — and exactly matches —
the underlying exact AMM math for both families and both directions.
"""

from __future__ import annotations

import pytest

from l2arb.amm import concentrated_liquidity as cl
from l2arb.amm import constant_product as cp
from l2arb.amm import quote
from l2arb.constants import Q96
from l2arb.errors import PoolStateError
from l2arb.model.blockstamp import Blockstamp
from l2arb.model.pool import PoolKind, PoolState, V2Reserves, V3Slot0
from l2arb.model.token import Token

pytestmark = pytest.mark.unit

CHAIN = 42161
BS = Blockstamp(chain_id=CHAIN, number=1, block_hash="0x" + "ab" * 32, timestamp=1)
WETH = Token(chain_id=CHAIN, address="0x" + "11" * 20, decimals=18, symbol="WETH")
USDC = Token(chain_id=CHAIN, address="0x" + "22" * 20, decimals=6, symbol="USDC")

V2 = PoolState(
    address="0x" + "aa" * 20,
    kind=PoolKind.CONSTANT_PRODUCT,
    token0=WETH,
    token1=USDC,
    fee_pips=3000,
    blockstamp=BS,
    v2=V2Reserves(reserve0=10 * 10**18, reserve1=30_000 * 10**6),
)
V3 = PoolState(
    address="0x" + "bb" * 20,
    kind=PoolKind.CONCENTRATED_LIQUIDITY,
    token0=WETH,
    token1=USDC,
    fee_pips=500,
    blockstamp=BS,
    v3=V3Slot0(sqrt_price_x96=2 * Q96, tick=0, liquidity=10**18),
)


def test_v2_marginal_and_amount_out_match_underlying() -> None:
    ri, ro = V2.oriented_v2_reserves(WETH.key)
    assert quote.marginal_rate(V2, WETH.key) == cp.marginal_rate(ri, ro, 3000)
    dx = 10**17
    assert quote.amount_out(V2, WETH.key, dx) == cp.amount_out_for_in(ri, ro, dx, 3000)
    # Reverse direction orients reserves the other way.
    ri2, ro2 = V2.oriented_v2_reserves(USDC.key)
    assert quote.amount_out(V2, USDC.key, 1000 * 10**6) == cp.amount_out_for_in(
        ri2, ro2, 1000 * 10**6, 3000
    )


def test_v3_marginal_and_amount_out_both_directions() -> None:
    sp, liq = V3.v3.sqrt_price_x96, V3.v3.liquidity  # type: ignore[union-attr]
    # token0 (WETH) in -> 0-for-1
    assert quote.marginal_rate(V3, WETH.key) == cl.marginal_rate_0_for_1(sp, 500)
    assert quote.amount_out(V3, WETH.key, 10**15) == cl.amount_out_0_for_1(sp, liq, 10**15, 500)
    # token1 (USDC) in -> 1-for-0
    assert quote.marginal_rate(V3, USDC.key) == cl.marginal_rate_1_for_0(sp, 500)
    assert quote.amount_out(V3, USDC.key, 10**6) == cl.amount_out_1_for_0(sp, liq, 10**6, 500)


def test_stableswap_dispatch() -> None:
    from l2arb.amm import stableswap as ss
    from l2arb.model.pool import StableSwapState

    pool = PoolState(
        address="0x" + "cc" * 20,
        kind=PoolKind.STABLESWAP,
        token0=WETH,
        token1=USDC,
        fee_pips=1000,
        blockstamp=BS,
        stable=StableSwapState(balance0=10**24, balance1=10**24, amp=200),
    )
    dx = 10**21
    assert quote.amount_out(pool, WETH.key, dx) == ss.amount_out(10**24, 10**24, 200, dx, 1000)
    assert quote.marginal_rate(pool, WETH.key) == ss.marginal_rate(10**24, 10**24, 200, 1000)


def test_weighted_dispatch() -> None:
    from l2arb.amm import weighted as wp
    from l2arb.model.pool import WeightedState

    pool = PoolState(
        address="0x" + "dd" * 20,
        kind=PoolKind.WEIGHTED,
        token0=WETH,
        token1=USDC,
        fee_pips=3000,
        blockstamp=BS,
        weighted=WeightedState(
            balance0=10**24, balance1=10**24, weight0=8 * 10**17, weight1=2 * 10**17
        ),
    )
    dx = 10**21
    # token0 (WETH) in -> weight_in=8e17, weight_out=2e17
    assert quote.amount_out(pool, WETH.key, dx) == wp.amount_out(
        10**24, 10**24, 8 * 10**17, 2 * 10**17, dx, 3000
    )
    assert quote.marginal_rate(pool, USDC.key) == wp.marginal_rate(
        10**24, 10**24, 2 * 10**17, 8 * 10**17, 3000
    )


def test_quote_rejects_foreign_token() -> None:
    foreign = (CHAIN, "0x" + "99" * 20)
    with pytest.raises(PoolStateError, match="not in pool"):
        quote.marginal_rate(V2, foreign)
    with pytest.raises(PoolStateError, match="not in pool"):
        quote.amount_out(V2, foreign, 1)
