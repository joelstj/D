"""Cross-chain 2-hop detection — the same asset, mispriced across two chains.

Scope is deliberately narrow (CLAUDE.md §1): **simple 2-hop only**, no cross-chain
cycles. Buy a canonical asset cheap on chain X (paying a canonical numeraire), move
it over a bridge, and sell it dear on chain Y (receiving the numeraire). Because
settlement is **not atomic**, this is a *detected spread*, not a guaranteed
capture — every opportunity carries ``settle_seconds`` and a cross-chain risk note
(docs/ARBITRAGE_THEORY §5).

Cost accounting is explicit:

    net = gross - bridge_cost - gas_cost      (gross measured pre-bridge, pre-gas)

Sizing optimises the *true* post-bridge profit (the bridge's proportional fee is
inside the route); the report breaks the costs back out. Only asset pairs the
:class:`AssetRegistry` marks fungible (same canonical id, both bridgeable) are ever
compared — "USDC" is never assumed to be one thing.
"""

from __future__ import annotations

from dataclasses import dataclass

from l2arb.amm import quote, sizing
from l2arb.detect.profit import ProfitContext, input_capacity
from l2arb.graph.rategraph import RateGraph
from l2arb.model.canonical_asset import AssetRegistry
from l2arb.model.opportunity import Leg, Opportunity, StrategyKind
from l2arb.model.pool import PoolState
from l2arb.model.token import Token, TokenKey

__all__ = ["BridgeModel", "BridgeQuote", "StaticBridgeModel", "cross_chain_two_hop"]


@dataclass(frozen=True, slots=True)
class BridgeQuote:
    """Cost + settlement time to move an asset across a bridge.

    ``fee_bps`` is proportional (basis points); ``fixed_fee`` is a flat cost in the
    asset's base units; ``settle_seconds`` is the (non-atomic) time to finality.
    """

    fee_bps: float
    fixed_fee: int
    settle_seconds: int

    def cost(self, amount: int) -> int:
        return self.fixed_fee + int(amount * self.fee_bps / 10_000.0)

    def net_after(self, amount: int) -> int:
        return max(0, amount - self.cost(amount))


class BridgeModel:
    """Interface: quote a bridge for ``symbol`` from one chain to another."""

    def quote(self, symbol: str, from_chain: int, to_chain: int, amount: int) -> BridgeQuote | None:
        raise NotImplementedError


class StaticBridgeModel(BridgeModel):
    """A config-driven bridge model keyed by ``(symbol, from_chain, to_chain)``."""

    def __init__(self, quotes: dict[tuple[str, int, int], BridgeQuote]) -> None:
        self._quotes = quotes

    def quote(self, symbol: str, from_chain: int, to_chain: int, amount: int) -> BridgeQuote | None:
        return self._quotes.get((symbol, from_chain, to_chain))


def _direct_pool(graph: RateGraph, src: TokenKey, dst: TokenKey) -> PoolState | None:
    """The best (lightest-weight) direct pool trading ``src -> dst``, if any."""
    edges = graph.edges_between(src, dst)
    if not edges:
        return None
    return graph.pool(min(edges, key=lambda e: e.log_weight).pool)


def _token_of(pool: PoolState, key: TokenKey) -> Token:
    return pool.token0 if key == pool.token0.key else pool.token1


def _gate_fails(pool: PoolState, ctx: ProfitContext) -> bool:
    """Whether ``pool`` must not be priced: unverified, or (when ``ctx`` opts in)
    stale. Shared by the pre-sizing gate in :func:`cross_chain_two_hop` and the
    defence-in-depth check in :func:`_build_opportunity` so both stay in lock-step
    with the same-chain gate in ``detect/profit.py`` (CLAUDE.md §3)."""
    if not pool.verified:
        return True
    now_ts, max_age = ctx.now_ts, ctx.max_pool_age_seconds
    return now_ts is not None and max_age is not None and pool.blockstamp.is_stale(now_ts, max_age)


def cross_chain_two_hop(
    buy_graph: RateGraph,
    sell_graph: RateGraph,
    *,
    asset_symbol: str,
    numeraire_symbol: str,
    registry: AssetRegistry,
    bridge: BridgeModel,
    buy_ctx: ProfitContext,
    sell_ctx: ProfitContext,
    min_profit_bps: float,
) -> Opportunity | None:
    """Detect a net-profitable cross-chain spread for one asset/numeraire pair.

    Prices the canonical ``asset_symbol`` in the canonical ``numeraire_symbol`` on
    both chains via direct pools, sizes the buy->bridge->sell route optimally, and
    reports it only when ``net`` clears ``min_profit_bps`` after bridge + gas.
    """
    asset = registry.asset(asset_symbol)
    num = registry.asset(numeraire_symbol)
    if asset is None or num is None:
        return None
    buy_chain, sell_chain = buy_graph.chain_id, sell_graph.chain_id
    asset_x, asset_y = asset.on_chain(buy_chain), asset.on_chain(sell_chain)
    num_x, num_y = num.on_chain(buy_chain), num.on_chain(sell_chain)
    if not (asset_x and asset_y and num_x and num_y):
        return None
    if not registry.are_fungible(asset_x.key, asset_y.key):
        return None  # the asset is not bridge-fungible across these chains
    # The profit math below subtracts the sell-chain numeraire output from the
    # buy-chain size, and feeds the bridged asset amount straight into the
    # sell-side swap — both assume a shared base-unit scale. That only holds when
    # the numeraire AND the bridged asset carry the *same decimals* on both
    # chains. If they differ (a real possibility — e.g. USDT is 18-dp on BNB Chain
    # but 6-dp elsewhere), the unmatched scale would fabricate an enormous phantom
    # profit stamped verified:true. There is no rescaling here, so exclude the
    # pair loudly rather than misprice it (CLAUDE.md §3 — "reject or flag", never
    # emit a phantom). The five shipped chains all use consistent decimals, so
    # this never fires there; it guards a new/misconfigured chain.
    if (
        num_x.token.decimals != num_y.token.decimals
        or asset_x.token.decimals != asset_y.token.decimals
    ):
        return None

    buy_pool = _direct_pool(buy_graph, num_x.key, asset_x.key)
    sell_pool = _direct_pool(sell_graph, asset_y.key, num_y.key)
    if buy_pool is None or sell_pool is None:
        return None
    # Gate BEFORE the size search prices anything — matching detect/profit.py and
    # the guarantee in docs/DATA_INTEGRITY.md that an unverified or stale pool is
    # rejected before any AMM/sizing math, not merely before emission.
    if _gate_fails(buy_pool, buy_ctx) or _gate_fails(sell_pool, sell_ctx):
        return None
    bquote = bridge.quote(asset_symbol, buy_chain, sell_chain, 1)
    if bquote is None:
        return None

    def route(size: int) -> int:
        bought = quote.amount_out(buy_pool, num_x.key, size)
        bridged = bquote.net_after(bought)
        return quote.amount_out(sell_pool, asset_y.key, bridged) if bridged > 0 else 0

    sized = sizing.optimal_size_auto(route, input_capacity(buy_pool, num_x.key))
    if sized.size <= 0 or sized.profit <= 0:
        return None
    return _build_opportunity(
        size=sized.size,
        buy_pool=buy_pool,
        sell_pool=sell_pool,
        num_x_key=num_x.key,
        asset_y_key=asset_y.key,
        num_y_key=num_y.key,
        bquote=bquote,
        buy_ctx=buy_ctx,
        sell_ctx=sell_ctx,
        min_profit_bps=min_profit_bps,
    )


def _build_opportunity(
    *,
    size: int,
    buy_pool: PoolState,
    sell_pool: PoolState,
    num_x_key: TokenKey,
    asset_y_key: TokenKey,
    num_y_key: TokenKey,
    bquote: BridgeQuote,
    buy_ctx: ProfitContext,
    sell_ctx: ProfitContext,
    min_profit_bps: float,
) -> Opportunity | None:
    # Defence in depth: cross_chain_two_hop already gates both pools *before* the
    # size search, so this never rejects in practice — it keeps _build_opportunity
    # safe to call standalone and never reports a spread resting on an unverified
    # or (when the context opts in) stale pool (CLAUDE.md §3).
    verified = buy_pool.verified and sell_pool.verified
    if _gate_fails(buy_pool, buy_ctx) or _gate_fails(sell_pool, sell_ctx):
        return None
    bought = quote.amount_out(buy_pool, num_x_key, size)
    bridged = bquote.net_after(bought)
    num_out = quote.amount_out(sell_pool, asset_y_key, bridged)
    num_out_no_bridge = quote.amount_out(sell_pool, asset_y_key, bought)

    gas_cost = buy_ctx.gas_cost_fn(num_x_key, 1) + sell_ctx.gas_cost_fn(num_y_key, 1)
    net_profit = num_out - size - gas_cost
    if net_profit <= 0:
        return None
    profit_bps = net_profit / size * 10_000.0
    if profit_bps < min_profit_bps:
        return None

    numeraire = _token_of(buy_pool, num_x_key)
    legs = (
        Leg(buy_pool.address, numeraire, buy_pool.other(num_x_key), size, bought),
        Leg(
            sell_pool.address,
            _token_of(sell_pool, asset_y_key),
            _token_of(sell_pool, num_y_key),
            bridged,
            num_out,
        ),
    )
    risk = buy_ctx.mev.assess(2, profit_bps, is_cross_chain=True)
    expected_net = int(net_profit * risk.capture_ratio * risk.success_probability)
    blockstamp = min((buy_pool.blockstamp, sell_pool.blockstamp), key=lambda b: b.number)
    return Opportunity(
        strategy=StrategyKind.CROSS_CHAIN_TWO_HOP,
        numeraire=numeraire,
        input_amount=size,
        output_amount=num_out,
        gross_profit=num_out_no_bridge - size,
        gas_cost=gas_cost,
        net_profit=net_profit,
        profit_bps=profit_bps,
        legs=legs,
        blockstamp=blockstamp,
        chain_ids=tuple(sorted({buy_pool.chain_id, sell_pool.chain_id})),
        risk=risk,
        score=float(expected_net),
        verified=verified,
        expected_net=expected_net,
        bridge_cost=num_out_no_bridge - num_out,
        settle_seconds=bquote.settle_seconds,
    )
