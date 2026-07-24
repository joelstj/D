"""Opportunity — a fully-priced, block-stamped arbitrage the engine reports.

Where a :data:`~l2arb.detect.cycle.Cycle` is a *hypothesis*, an :class:`Opportunity`
is the confirmed, size-optimised result: exact input/output at the optimal trade
size, deterministic costs (gas), and a **risk assessment** capturing the execution
reality the brief asks us to model — MEV competition, front-/back-running, and the
resulting probability the trade actually lands (docs/ARBITRAGE_THEORY §4).

This system is a **detector**: it never executes. The risk fields exist so a
downstream executor can prioritise the opportunities most likely to fill
profitably; ``score`` is the risk-adjusted expected value used for top-N ranking.
Every opportunity carries full provenance (pools, block) so it can be replayed and
verified (CLAUDE.md §3).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from l2arb.model.blockstamp import Blockstamp
from l2arb.model.token import Token

__all__ = ["Leg", "Opportunity", "RiskAssessment", "StrategyKind"]


class StrategyKind(Enum):
    """Which arbitrage shape produced this opportunity."""

    TWO_HOP = "two_hop"
    TRIANGULAR = "triangular"
    MULTI_HOP = "multi_hop"
    CROSS_CHAIN_TWO_HOP = "cross_chain_two_hop"


@dataclass(frozen=True, slots=True)
class Leg:
    """One hop of the route: swap ``amount_in`` of ``token_in`` for ``amount_out``."""

    pool_address: str
    token_in: Token
    token_out: Token
    amount_in: int
    amount_out: int


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    """Execution-likelihood scoring for an opportunity (detection context only).

    * ``success_probability`` — estimated chance the trade lands profitably given
      hop count, freshness, and competition; in ``[0, 1]``.
    * ``capture_ratio`` — fraction of the gross edge expected to survive MEV
      competition (priority-fee auctions / back-running); in ``[0, 1]``.
    * ``frontrun_risk`` — estimated chance the edge is taken first; in ``[0, 1]``.
    * ``notes`` — human-readable factors that drove the score.
    """

    success_probability: float
    capture_ratio: float
    frontrun_risk: float
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Opportunity:
    """A confirmed, net-profitable, size-optimised arbitrage with provenance."""

    strategy: StrategyKind
    numeraire: Token
    input_amount: int
    output_amount: int
    gross_profit: int
    gas_cost: int
    net_profit: int
    profit_bps: float
    legs: tuple[Leg, ...]
    blockstamp: Blockstamp
    chain_ids: tuple[int, ...]
    risk: RiskAssessment
    score: float
    verified: bool = False
    expected_net: int = 0
    # Cross-chain only: bridge cost (in the numeraire) and non-atomic settlement
    # time. Zero for single-chain opportunities, so ``net = gross - gas - bridge``
    # reduces to ``net = gross - gas`` there.
    bridge_cost: int = 0
    settle_seconds: int = 0

    @property
    def hops(self) -> int:
        return len(self.legs)

    @property
    def is_cross_chain(self) -> bool:
        return len(self.chain_ids) > 1

    @property
    def pool_addresses(self) -> tuple[str, ...]:
        return tuple(leg.pool_address for leg in self.legs)
