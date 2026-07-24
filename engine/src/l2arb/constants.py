"""Shared fixed-point constants for exact on-chain math.

These are protocol constants, not tunables — they mirror the on-chain contracts
bit-for-bit and must never be "adjusted." They live in one place so the model
layer and the AMM math layer share a single definition.

**Precision note (load-bearing):** all exact AMM math runs in Python's
arbitrary-precision ``int``. On-chain reserves reach 2**112 (V2) and
``sqrtPriceX96`` reaches ~2**160 (V3) — far beyond int64 — so the exact path must
**never** be lowered to ``numba``/``numpy`` fixed-width integers. ``numba`` is
reserved for the float ``-ln(rate)`` graph search, where 64-bit is fine.
"""

from __future__ import annotations

__all__ = [
    "FEE_DENOMINATOR",
    "MAX_SQRT_RATIO",
    "MAX_TICK",
    "MIN_SQRT_RATIO",
    "MIN_TICK",
    "Q96",
    "Q192",
    "WEIGHT_UNIT",
]

# Unified fee scale: fee = fee_pips / FEE_DENOMINATOR (parts per million).
FEE_DENOMINATOR = 1_000_000

# Balancer weighted-pool weight unit (its "ONE"): weights are fixed-point out of
# 10**18, so an 80/20 pool is (8*10**17, 2*10**17). Only the ratio w_in/w_out is
# used in pricing, so any consistent unit works; this documents the convention.
WEIGHT_UNIT = 10**18

# Uniswap V3 Q64.96 fixed point: sqrtPriceX96 = sqrt(price) * 2**96.
Q96 = 2**96
Q192 = 2**192  # (2**96)**2 — used to square sqrtPriceX96 into a price ratio.

# TickMath bounds (Uniswap V3 core). A tick / sqrt-ratio outside these is invalid.
MIN_TICK = -887_272
MAX_TICK = 887_272
MIN_SQRT_RATIO = 4_295_128_739
MAX_SQRT_RATIO = 1_461_446_703_485_210_103_287_273_052_203_988_822_378_723_970_342
