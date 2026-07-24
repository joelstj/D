"""Test-only factory helpers for building pools and planted-arbitrage graphs.

Importable as ``graphkit`` (``tests`` is on the test/mypy path). Everything here
is **synthetic** and lives strictly in the test tree — it is never importable by
``src/l2arb`` runtime code (docs/DATA_INTEGRITY §3, enforced by the scope guards).
"""

from __future__ import annotations

from l2arb.constants import Q96
from l2arb.detect.profit import GasModel, ProfitContext
from l2arb.graph.rategraph import RateGraph
from l2arb.model.blockstamp import Blockstamp
from l2arb.model.pool import (
    PoolKind,
    PoolState,
    StableSwapState,
    V2Reserves,
    V3Slot0,
    WeightedState,
)
from l2arb.model.token import Token

CHAIN = 42161
BS = Blockstamp(chain_id=CHAIN, number=1, block_hash="0x" + "ab" * 32, timestamp=1_700_000_000)


class GraphKit:
    """Factory helpers for tokens, pools, and rate graphs in tests."""

    CHAIN = CHAIN
    BS = BS
    UNITY_SQRT = Q96

    @staticmethod
    def token(n: int, decimals: int = 18, chain: int = CHAIN) -> Token:
        return Token(chain_id=chain, address=f"0x{n:040x}", decimals=decimals, symbol=f"T{n}")

    @staticmethod
    def blockstamp(chain: int = CHAIN, number: int = 1) -> Blockstamp:
        return Blockstamp(
            chain_id=chain, number=number, block_hash="0x" + "ab" * 32, timestamp=1_700_000_000
        )

    @staticmethod
    def v2(n: int, t0: Token, t1: Token, r0: int, r1: int, fee: int = 3000) -> PoolState:
        return PoolState(
            address=f"0x{n:040x}",
            kind=PoolKind.CONSTANT_PRODUCT,
            token0=t0,
            token1=t1,
            fee_pips=fee,
            blockstamp=GraphKit.blockstamp(t0.chain_id),
            v2=V2Reserves(reserve0=r0, reserve1=r1),
        )

    @staticmethod
    def v3(
        n: int, t0: Token, t1: Token, sqrt_price_x96: int, liquidity: int, fee: int = 500
    ) -> PoolState:
        return PoolState(
            address=f"0x{n:040x}",
            kind=PoolKind.CONCENTRATED_LIQUIDITY,
            token0=t0,
            token1=t1,
            fee_pips=fee,
            blockstamp=GraphKit.blockstamp(t0.chain_id),
            v3=V3Slot0(sqrt_price_x96=sqrt_price_x96, tick=0, liquidity=liquidity),
        )

    @staticmethod
    def stable(
        n: int, t0: Token, t1: Token, bal0: int, bal1: int, amp: int = 200, fee: int = 1000
    ) -> PoolState:
        return PoolState(
            address=f"0x{n:040x}",
            kind=PoolKind.STABLESWAP,
            token0=t0,
            token1=t1,
            fee_pips=fee,
            blockstamp=GraphKit.blockstamp(t0.chain_id),
            stable=StableSwapState(balance0=bal0, balance1=bal1, amp=amp),
        )

    @staticmethod
    def weighted(
        n: int,
        t0: Token,
        t1: Token,
        bal0: int,
        bal1: int,
        weight0: int = 5 * 10**17,
        weight1: int = 5 * 10**17,
        fee: int = 3000,
    ) -> PoolState:
        return PoolState(
            address=f"0x{n:040x}",
            kind=PoolKind.WEIGHTED,
            token0=t0,
            token1=t1,
            fee_pips=fee,
            blockstamp=GraphKit.blockstamp(t0.chain_id),
            weighted=WeightedState(balance0=bal0, balance1=bal1, weight0=weight0, weight1=weight1),
        )

    @staticmethod
    def graph(pools: list[PoolState], chain: int = CHAIN) -> RateGraph:
        g = RateGraph(chain)
        for pool in pools:
            g.upsert_pool(pool)
        return g

    @staticmethod
    def profit_ctx(gas_price_wei: int = 10**6, min_bps: float = 1.0) -> ProfitContext:
        """A profit context treating the numeraire as the 18-dp native gas token."""
        gas = GasModel(
            gas_price_wei=gas_price_wei,
            base_gas=100_000,
            per_hop_gas=80_000,
            safety_multiplier=1.5,
        )
        return ProfitContext(gas_cost_fn=gas.cost_fn(lambda _key: 1.0), min_profit_bps=min_bps)
