"""The net-profit gate — turn a candidate cycle into a reported opportunity.

This is stage two of the two-stage pipeline (ADR-005). A candidate cycle is
re-priced with **exact** integer AMM math at the **optimal** trade size, then
gated on real economics (docs/ARBITRAGE_THEORY §4):

    net = out(s*) - s* - gas(hops)          (slippage is already inside out(s*))

An opportunity is reported only when ``net > 0`` **and** it clears
``min_profit_bps`` of the input. Slippage is exact (it lives in the AMM math);
gas is modelled as L2 execution + L1 data cost converted to the numeraire via an
**on-chain** price (never a guess) with a safety multiplier.

Beyond the deterministic net, every opportunity gets a **risk assessment** — the
MEV / front-running / back-running reality the brief asks us to model. We do not
execute, so this estimates the probability the trade lands and the share of the
edge that survives competition; ``score`` (risk-adjusted expected value) drives
top-N ranking. Reporting a cycle that would lose money after costs is the cardinal
sin here, pinned by property tests (T-0409).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from typing import cast

from l2arb.amm import concentrated_liquidity as cl
from l2arb.amm import constant_product as cp
from l2arb.amm import quote, sizing
from l2arb.amm import stableswap as ss
from l2arb.amm import weighted as wp
from l2arb.detect.cycle import Cycle
from l2arb.graph.rategraph import RateGraph
from l2arb.model.blockstamp import Blockstamp
from l2arb.model.opportunity import Leg, Opportunity, RiskAssessment, StrategyKind
from l2arb.model.pool import PoolKind, PoolState, V3Slot0
from l2arb.model.token import Token, TokenKey

__all__ = ["GasModel", "MevModel", "ProfitContext", "evaluate", "input_capacity"]

# numeraire base units per 1 native (gas-token) wei — sourced on-chain by the engine.
NativePrice = Callable[[TokenKey], float]
# (numeraire token key, hop count) -> gas cost in numeraire base units.
GasCostFn = Callable[[TokenKey, int], int]


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass(frozen=True, slots=True)
class GasModel:
    """L2 gas cost model: execution gas + L1 data-availability, with a haircut.

    ``gas_price_wei`` and ``l1_data_fee_wei`` come from live chain reads. The cost
    is converted to the numeraire by a caller-supplied on-chain price so the gate
    never guesses a token price.
    """

    gas_price_wei: int
    base_gas: int = 150_000
    per_hop_gas: int = 100_000
    l1_data_fee_wei: int = 0
    safety_multiplier: float = 1.5

    def gas_units(self, hops: int) -> int:
        return self.base_gas + self.per_hop_gas * hops

    def cost_wei(self, hops: int) -> int:
        raw = self.gas_units(hops) * self.gas_price_wei + self.l1_data_fee_wei
        return int(raw * self.safety_multiplier)

    def cost_fn(self, price_native_in: NativePrice) -> GasCostFn:
        """Build a ``(numeraire, hops) -> numeraire-base-units`` gas-cost function."""

        def _cost(numeraire: TokenKey, hops: int) -> int:
            return int(self.cost_wei(hops) * price_native_in(numeraire))

        return _cost


@dataclass(frozen=True, slots=True)
class MevModel:
    """Execution-risk model for MEV competition and front-/back-running.

    Heuristic and configurable — it does not claim to predict the mempool, only to
    rank opportunities by how likely they are to land profitably. Bigger, more
    obvious edges attract more competition (lower ``capture``); more hops and
    cross-chain settlement lower the success probability.
    """

    base_success_probability: float = 0.9
    per_hop_success_penalty: float = 0.04
    cross_chain_success_penalty: float = 0.25
    base_capture_ratio: float = 0.7
    competition_scale_bps: float = 50.0

    def assess(self, hops: int, profit_bps: float, is_cross_chain: bool) -> RiskAssessment:
        success = self.base_success_probability - self.per_hop_success_penalty * max(0, hops - 2)
        if is_cross_chain:
            success -= self.cross_chain_success_penalty
        success = _clamp(success, 0.05, 0.99)
        capture = self.base_capture_ratio / (1.0 + profit_bps / self.competition_scale_bps)
        capture = _clamp(capture, 0.05, 1.0)
        frontrun = _clamp(1.0 - success, 0.0, 1.0)
        notes = (
            f"hops={hops}",
            f"cross_chain={is_cross_chain}",
            f"edge_bps={profit_bps:.1f}",
        )
        return RiskAssessment(success, capture, frontrun, notes)


@dataclass(frozen=True, slots=True)
class ProfitContext:
    """Everything the gate needs beyond the graph: gas, thresholds, risk model."""

    gas_cost_fn: GasCostFn
    min_profit_bps: float = 5.0
    mev: MevModel = field(default_factory=MevModel)
    seed_size_hint: int | None = None


def input_capacity(pool: PoolState, token_in_key: TokenKey) -> int:
    """A rough size scale to seed the auto-bracketed size search."""
    if pool.kind is PoolKind.CONSTANT_PRODUCT:
        reserve_in, _ = pool.oriented_v2_reserves(token_in_key)
        return reserve_in
    if pool.kind is PoolKind.STABLESWAP:
        return pool.oriented_stable(token_in_key)[0]
    if pool.kind is PoolKind.WEIGHTED:
        return pool.oriented_weighted(token_in_key)[0]
    return cast(V3Slot0, pool.v3).liquidity  # non-None for V3 (PoolState invariant)


def _compile_route(cycle: Cycle, graph: RateGraph) -> Callable[[int], int]:
    """Precompute each hop's exact-math step once for a fast size-search route.

    The size solver evaluates the route dozens of times per candidate; hoisting
    the per-hop reserve orientation, direction, and fee out of that loop (and not
    allocating ``Leg`` objects during the search) is the single biggest hot-path
    win. The returned closure is pure arithmetic over the precomputed steps.
    """
    steps: list[Callable[[int], int]] = []
    for edge in cycle:
        pool = graph.pool(edge.pool)
        if pool.kind is PoolKind.CONSTANT_PRODUCT:
            reserve_in, reserve_out = pool.oriented_v2_reserves(edge.src)
            # State is validated at PoolState construction, so skip re-validation
            # on every size-search evaluation (the hot path).
            steps.append(
                partial(cp.amount_out_unchecked, reserve_in, reserve_out, fee_pips=pool.fee_pips)
            )
        elif pool.kind is PoolKind.STABLESWAP:
            bal_in, bal_out, amp = pool.oriented_stable(edge.src)
            steps.append(partial(ss.amount_out, bal_in, bal_out, amp, fee_pips=pool.fee_pips))
        elif pool.kind is PoolKind.WEIGHTED:
            bal_in, bal_out, w_in, w_out = pool.oriented_weighted(edge.src)
            steps.append(
                partial(wp.amount_out, bal_in, bal_out, w_in, w_out, fee_pips=pool.fee_pips)
            )
        else:
            v3 = cast(V3Slot0, pool.v3)
            fn = cl.amount_out_0_for_1 if pool.is_token0_input(edge.src) else cl.amount_out_1_for_0
            steps.append(partial(fn, v3.sqrt_price_x96, v3.liquidity, fee_pips=pool.fee_pips))

    def route(size: int) -> int:
        amount = size
        for step in steps:
            amount = step(amount)
        return amount

    return route


def _walk_route(cycle: Cycle, graph: RateGraph, amount_in: int) -> tuple[int, list[Leg]]:
    """Chain the exact swaps through the cycle, recording each leg (called once)."""
    amount = amount_in
    legs: list[Leg] = []
    for edge in cycle:
        pool = graph.pool(edge.pool)
        token_in = pool.token0 if edge.src == pool.token0.key else pool.token1
        token_out = pool.other(edge.src)
        out = quote.amount_out(pool, edge.src, amount)
        legs.append(Leg(pool.address, token_in, token_out, amount, out))
        amount = out
    return amount, legs


def _binding_blockstamp(cycle: Cycle, graph: RateGraph) -> Blockstamp:
    """The stalest (min block number) input — the opportunity is only as fresh as it."""
    return min((graph.pool(e.pool).blockstamp for e in cycle), key=lambda b: b.number)


def _numeraire_token(cycle: Cycle, graph: RateGraph) -> Token:
    first = graph.pool(cycle[0].pool)
    return first.token0 if cycle[0].src == first.token0.key else first.token1


def evaluate(
    cycle: Cycle,
    graph: RateGraph,
    strategy: StrategyKind,
    ctx: ProfitContext,
) -> Opportunity | None:
    """Re-price ``cycle`` exactly at the optimal size and gate on net profit.

    Returns a fully-populated :class:`Opportunity`, or ``None`` when no size makes
    it net-profitable above ``min_profit_bps``. Never reports a losing cycle.
    """
    if not cycle:
        return None
    numeraire = _numeraire_token(cycle, graph)
    numeraire_key = numeraire.key

    route = _compile_route(cycle, graph)
    seed = ctx.seed_size_hint or input_capacity(graph.pool(cycle[0].pool), numeraire_key)
    sized = sizing.optimal_size_auto(route, seed)
    if sized.size <= 0 or sized.profit <= 0:
        return None

    size, gross_profit = sized.size, sized.profit
    hops = len(cycle)
    gas_cost = ctx.gas_cost_fn(numeraire_key, hops)
    net_profit = gross_profit - gas_cost
    profit_bps = net_profit / size * 10_000.0
    if net_profit <= 0 or profit_bps < ctx.min_profit_bps:
        return None

    _, legs = _walk_route(cycle, graph, size)
    chain_ids = tuple(sorted({graph.pool(e.pool).chain_id for e in cycle}))
    is_cross_chain = len(chain_ids) > 1
    risk = ctx.mev.assess(hops, profit_bps, is_cross_chain)
    expected_net = int(net_profit * risk.capture_ratio * risk.success_probability)
    verified = all(graph.pool(e.pool).verified for e in cycle)

    return Opportunity(
        strategy=strategy,
        numeraire=numeraire,
        input_amount=size,
        output_amount=legs[-1].amount_out,
        gross_profit=gross_profit,
        gas_cost=gas_cost,
        net_profit=net_profit,
        profit_bps=profit_bps,
        legs=tuple(legs),
        blockstamp=_binding_blockstamp(cycle, graph),
        chain_ids=chain_ids,
        risk=risk,
        score=float(expected_net),
        verified=verified,
        expected_net=expected_net,
    )
