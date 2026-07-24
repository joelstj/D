"""Summary metrics over a replay — the offline analytics report.

Lean, stdlib-only aggregates that characterise how the engine performed over a
historical window: how often it found something, the profit distribution, the
strategy mix, and edge decay (how quickly an opportunity's edge fades block to
block). The heavy quant stack (``quantstats`` / ``PyPortfolioOpt`` etc.) is the
optional ``analytics`` extra; this module deliberately needs none of it so the
core stays lean and the report always runs.
"""

from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Sequence
from itertools import pairwise
from typing import Any

from l2arb.backtest.replay import ReplayResult

__all__ = ["summarize"]


def summarize(results: Sequence[ReplayResult]) -> dict[str, Any]:
    """Aggregate replay results into a JSON-safe metrics report.

    Big-integer profits are reported as decimal strings. Returns zeros for an empty
    replay rather than raising.
    """
    all_opps = [opp for r in results for opp in r.opportunities]
    blocks = len(results)
    blocks_with_opp = sum(1 for r in results if r.opportunities)
    net_profits = [opp.net_profit for opp in all_opps]
    profit_bps = [opp.profit_bps for opp in all_opps]
    strategy_mix = Counter(opp.strategy.value for opp in all_opps)

    report: dict[str, Any] = {
        "blocks": blocks,
        "opportunities": len(all_opps),
        "blocks_with_opportunity": blocks_with_opp,
        "hit_rate": (blocks_with_opp / blocks) if blocks else 0.0,
        "strategy_mix": dict(strategy_mix),
        "total_net_profit": str(sum(net_profits)),
        "max_net_profit": str(max(net_profits)) if net_profits else "0",
        "mean_net_profit": str(int(statistics.mean(net_profits))) if net_profits else "0",
        "mean_profit_bps": statistics.fmean(profit_bps) if profit_bps else 0.0,
        "max_profit_bps": max(profit_bps) if profit_bps else 0.0,
        "mean_top_score_decay": _mean_top_score_decay(results),
    }
    return report


def _mean_top_score_decay(results: Sequence[ReplayResult]) -> float:
    """Mean block-over-block change in the best opportunity's score.

    Negative means the top edge tended to fade between blocks (opportunities are
    short-lived); ~0 means persistent edges. Uses only consecutive blocks that both
    reported at least one opportunity.
    """
    tops = [max((o.score for o in r.opportunities), default=None) for r in results]
    deltas = [nxt - cur for cur, nxt in pairwise(tops) if cur is not None and nxt is not None]
    return statistics.fmean(deltas) if deltas else 0.0
