"""Unit tests for pool state value objects (:mod:`l2arb.model.pool`)."""

from __future__ import annotations

import pytest

from l2arb.errors import PoolStateError
from l2arb.model.blockstamp import Blockstamp
from l2arb.model.pool import (
    MAX_SQRT_RATIO,
    PoolKind,
    PoolState,
    StableSwapState,
    V2Reserves,
    V3Slot0,
    WeightedState,
)
from l2arb.model.token import Token

pytestmark = pytest.mark.unit

CHAIN = 42161
BS = Blockstamp(chain_id=CHAIN, number=1, block_hash="0x" + "ab" * 32, timestamp=1_700_000_000)
WETH = Token(chain_id=CHAIN, address="0x" + "11" * 20, decimals=18, symbol="WETH")
USDC = Token(chain_id=CHAIN, address="0x" + "22" * 20, decimals=6, symbol="USDC")


def _v2(**over: object) -> PoolState:
    kw: dict[str, object] = {
        "address": "0x" + "aa" * 20,
        "kind": PoolKind.CONSTANT_PRODUCT,
        "token0": WETH,
        "token1": USDC,
        "fee_pips": 3000,
        "blockstamp": BS,
        "v2": V2Reserves(reserve0=10 * 10**18, reserve1=30_000 * 10**6),
    }
    kw.update(over)
    return PoolState(**kw)  # type: ignore[arg-type]


def _v3(**over: object) -> PoolState:
    kw: dict[str, object] = {
        "address": "0x" + "bb" * 20,
        "kind": PoolKind.CONCENTRATED_LIQUIDITY,
        "token0": WETH,
        "token1": USDC,
        "fee_pips": 500,
        "blockstamp": BS,
        "v3": V3Slot0(sqrt_price_x96=2**96, tick=0, liquidity=10**18),
    }
    kw.update(over)
    return PoolState(**kw)  # type: ignore[arg-type]


# --- V2Reserves / V3Slot0 primitives ------------------------------------- #
def test_v2_reserves_reject_negative() -> None:
    with pytest.raises(PoolStateError):
        V2Reserves(reserve0=-1, reserve1=1)


def test_v2_tradable() -> None:
    assert V2Reserves(1, 1).tradable is True
    assert V2Reserves(0, 1).tradable is False


def test_v3_slot0_validation() -> None:
    with pytest.raises(PoolStateError):
        V3Slot0(sqrt_price_x96=0, tick=0, liquidity=1)
    with pytest.raises(PoolStateError):
        V3Slot0(sqrt_price_x96=MAX_SQRT_RATIO + 1, tick=0, liquidity=1)
    with pytest.raises(PoolStateError):
        V3Slot0(sqrt_price_x96=2**96, tick=10**9, liquidity=1)
    with pytest.raises(PoolStateError):
        V3Slot0(sqrt_price_x96=2**96, tick=0, liquidity=-1)


def test_v3_tradable_requires_liquidity() -> None:
    assert V3Slot0(2**96, 0, 0).tradable is False
    assert V3Slot0(2**96, 0, 1).tradable is True


# --- PoolState construction / validation --------------------------------- #
def test_v2_pool_ok() -> None:
    p = _v2()
    assert p.chain_id == CHAIN
    assert p.tradable is True
    assert p.token_keys == (WETH.key, USDC.key)


def test_v3_pool_ok() -> None:
    p = _v3()
    assert p.kind is PoolKind.CONCENTRATED_LIQUIDITY
    assert p.tradable is True


def test_identical_tokens_rejected() -> None:
    with pytest.raises(PoolStateError, match="must differ"):
        _v2(token1=WETH)


def test_cross_chain_tokens_rejected() -> None:
    other = Token(chain_id=8453, address="0x" + "33" * 20, decimals=6)
    with pytest.raises(PoolStateError, match="same chain"):
        _v2(token1=other)


def test_blockstamp_chain_must_match() -> None:
    bad_bs = Blockstamp(chain_id=8453, number=1, block_hash="0x" + "cd" * 32, timestamp=1)
    with pytest.raises(PoolStateError, match="blockstamp chain"):
        _v2(blockstamp=bad_bs)


@pytest.mark.parametrize("fee", [-1, 1_000_000, 2_000_000])
def test_fee_range(fee: int) -> None:
    with pytest.raises(PoolStateError, match="fee_pips"):
        _v2(fee_pips=fee)


def test_v2_kind_requires_v2_state() -> None:
    with pytest.raises(PoolStateError):
        _v2(v2=None)
    with pytest.raises(PoolStateError):
        PoolState(
            address="0x" + "aa" * 20,
            kind=PoolKind.CONSTANT_PRODUCT,
            token0=WETH,
            token1=USDC,
            fee_pips=3000,
            blockstamp=BS,
            v3=V3Slot0(2**96, 0, 1),
        )


def test_v3_kind_requires_v3_state() -> None:
    with pytest.raises(PoolStateError):
        _v3(v3=None)
    with pytest.raises(PoolStateError):
        PoolState(
            address="0x" + "bb" * 20,
            kind=PoolKind.CONCENTRATED_LIQUIDITY,
            token0=WETH,
            token1=USDC,
            fee_pips=500,
            blockstamp=BS,
            v2=V2Reserves(1, 1),
        )


# --- orientation helpers -------------------------------------------------- #
def test_contains_and_other() -> None:
    p = _v2()
    assert p.contains(WETH.key)
    assert p.contains(USDC.key)
    assert not p.contains((CHAIN, "0x" + "99" * 20))
    assert p.other(WETH.key) == USDC
    assert p.other(USDC.key) == WETH
    with pytest.raises(PoolStateError):
        p.other((CHAIN, "0x" + "99" * 20))


def test_is_token0_input() -> None:
    p = _v2()
    assert p.is_token0_input(WETH.key) is True
    assert p.is_token0_input(USDC.key) is False
    with pytest.raises(PoolStateError):
        p.is_token0_input((CHAIN, "0x" + "99" * 20))


def test_oriented_v2_reserves() -> None:
    p = _v2()  # reserve0=10 WETH, reserve1=30k USDC
    assert p.oriented_v2_reserves(WETH.key) == (10 * 10**18, 30_000 * 10**6)
    assert p.oriented_v2_reserves(USDC.key) == (30_000 * 10**6, 10 * 10**18)


def test_oriented_v2_reserves_wrong_kind() -> None:
    with pytest.raises(PoolStateError, match="only valid for V2"):
        _v3().oriented_v2_reserves(WETH.key)


# --- StableSwap / Weighted state objects --------------------------------- #
def _stable(**over: object) -> PoolState:
    kw: dict[str, object] = {
        "address": "0x" + "cc" * 20,
        "kind": PoolKind.STABLESWAP,
        "token0": WETH,
        "token1": USDC,
        "fee_pips": 1000,
        "blockstamp": BS,
        "stable": StableSwapState(balance0=10**24, balance1=10**24, amp=200),
    }
    kw.update(over)
    return PoolState(**kw)  # type: ignore[arg-type]


def _weighted(**over: object) -> PoolState:
    kw: dict[str, object] = {
        "address": "0x" + "dd" * 20,
        "kind": PoolKind.WEIGHTED,
        "token0": WETH,
        "token1": USDC,
        "fee_pips": 3000,
        "blockstamp": BS,
        "weighted": WeightedState(
            balance0=10**24, balance1=10**24, weight0=8 * 10**17, weight1=2 * 10**17
        ),
    }
    kw.update(over)
    return PoolState(**kw)  # type: ignore[arg-type]


def test_stableswap_state_validation() -> None:
    with pytest.raises(PoolStateError):
        StableSwapState(balance0=-1, balance1=1, amp=200)
    with pytest.raises(PoolStateError):
        StableSwapState(balance0=1, balance1=1, amp=0)
    assert StableSwapState(1, 1, 200).tradable is True
    assert StableSwapState(0, 1, 200).tradable is False


def test_weighted_state_validation() -> None:
    with pytest.raises(PoolStateError):
        WeightedState(balance0=-1, balance1=1, weight0=1, weight1=1)
    with pytest.raises(PoolStateError):
        WeightedState(balance0=1, balance1=1, weight0=0, weight1=1)
    assert WeightedState(1, 1, 1, 1).tradable is True
    assert WeightedState(1, 0, 1, 1).tradable is False


def test_stable_and_weighted_pools_construct_and_orient() -> None:
    s = _stable()
    assert s.kind is PoolKind.STABLESWAP
    assert s.tradable is True
    assert s.oriented_stable(WETH.key) == (10**24, 10**24, 200)
    assert s.oriented_stable(USDC.key) == (10**24, 10**24, 200)

    w = _weighted()
    assert w.tradable is True
    assert w.oriented_weighted(WETH.key) == (10**24, 10**24, 8 * 10**17, 2 * 10**17)
    assert w.oriented_weighted(USDC.key) == (10**24, 10**24, 2 * 10**17, 8 * 10**17)


def test_wrong_kind_orientation_guards() -> None:
    with pytest.raises(PoolStateError, match="only valid for StableSwap"):
        _v2().oriented_stable(WETH.key)
    with pytest.raises(PoolStateError, match="only valid for Weighted"):
        _v2().oriented_weighted(WETH.key)


def test_kind_state_mismatch_rejected() -> None:
    # STABLESWAP kind but carrying a V2 state -> rejected.
    with pytest.raises(PoolStateError, match="must carry exactly"):
        PoolState(
            address="0x" + "cc" * 20,
            kind=PoolKind.STABLESWAP,
            token0=WETH,
            token1=USDC,
            fee_pips=1000,
            blockstamp=BS,
            v2=V2Reserves(1, 1),
        )
    # Two states present -> rejected.
    with pytest.raises(PoolStateError, match="must carry exactly"):
        _weighted(stable=StableSwapState(1, 1, 200))
