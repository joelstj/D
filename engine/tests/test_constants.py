"""Pin the shared fixed-point constants against their protocol definitions.

These are on-chain constants, not tunables — a change here silently corrupts
every quote, so the values and their relationships are asserted explicitly.
"""

from __future__ import annotations

import pytest

from l2arb import constants as k

pytestmark = pytest.mark.unit


def test_fee_denominator_is_one_million() -> None:
    assert k.FEE_DENOMINATOR == 1_000_000
    # 0.30 % as pips reproduces the classic Uniswap V2 997/1000 ratio exactly.
    assert (k.FEE_DENOMINATOR - 3000) / k.FEE_DENOMINATOR == 997 / 1000


def test_q96_and_q192() -> None:
    assert k.Q96 == 2**96
    assert k.Q192 == 2**192
    assert k.Q192 == k.Q96**2


def test_tick_bounds_are_symmetric() -> None:
    assert k.MIN_TICK == -k.MAX_TICK
    assert k.MAX_TICK == 887_272


def test_sqrt_ratio_bounds_match_uniswap_tickmath() -> None:
    # The exact TickMath constants from Uniswap V3 core.
    assert k.MIN_SQRT_RATIO == 4_295_128_739
    assert k.MAX_SQRT_RATIO == 1_461_446_703_485_210_103_287_273_052_203_988_822_378_723_970_342
    # A price at the reference tick 0 (sqrtP = 2**96) sits strictly inside.
    assert k.MIN_SQRT_RATIO < k.Q96 < k.MAX_SQRT_RATIO
