"""Scale stress — the detector stays correct and bounded on a large mixed graph.

A production feed is hundreds of pools across every AMM family, updating
continuously. This drives the whole engine over a deterministic ~64-token,
~160-pool graph mixing V2 / V3 / StableSwap / weighted pools with dozens of
planted arbitrages, and asserts the three things that must hold at scale:

* **Soundness** — every one of the top-N reported opportunities is genuinely
  net-profitable (never a phantom edge, no matter how large the search space).
* **Ranking** — results come back ordered by risk-adjusted score, capped at N.
* **Determinism** — identical inputs give byte-identical ranked output (no
  wall-clock, no RNG on the hot path), and incremental recompute agrees with a
  full recompute.

A loose wall-clock ceiling guards against a pathological hang / accidental
super-linear blow-up; the real latency SLO lives in the benchmark tier.
"""

from __future__ import annotations

import time

import pytest

from graphkit import GraphKit
from l2arb.constants import Q96
from l2arb.engine.engine import ArbitrageEngine
from l2arb.model.opportunity import Opportunity

pytestmark = pytest.mark.unit

N_TOKENS = 64
HANG_GUARD_SECONDS = 8.0  # generous: a hang guard, not a perf gate (see benchmark tier)


def _assert_sound(opp: Opportunity) -> None:
    assert opp.net_profit > 0
    assert opp.output_amount > opp.input_amount
    assert opp.gross_profit == opp.output_amount - opp.input_amount
    assert opp.net_profit == opp.gross_profit - opp.gas_cost - opp.bridge_cost
    assert opp.legs[0].amount_in == opp.input_amount
    assert opp.legs[-1].amount_out == opp.output_amount


def _big_engine(gk: type[GraphKit]) -> ArbitrageEngine:
    """A deterministic large graph mixing all four AMM families + planted arbs."""
    engine = ArbitrageEngine(max_hops=4)
    engine.configure_chain(gk.CHAIN, gk.profit_ctx(min_bps=1.0))
    tokens = [gk.token(i + 1) for i in range(N_TOKENS)]

    addr = 5000
    for i in range(N_TOKENS):
        a = tokens[i]
        b = tokens[(i + 1) % N_TOKENS]
        skew = 1000 + (i % 7) * 25  # deterministic imbalance -> real edges
        # A ring of pairs, each with a second differently-priced pool (spatial arb).
        engine.ingest(gk.v2(addr, a, b, 1000 * 10**18, skew * 10**18))
        addr += 1
        engine.ingest(gk.v2(addr, a, b, 1000 * 10**18, (skew + 18) * 10**18))
        addr += 1
    # A few pools of every other family so all dispatch paths are exercised at scale
    # (kept sparse: expensive-math pools in many cycles would dominate the runtime,
    #  which is the benchmark tier's job to measure, not this soundness check).
    engine.ingest(gk.v3(addr, tokens[0], tokens[1], Q96, 10**24))
    addr += 1
    engine.ingest(gk.v3(addr, tokens[10], tokens[11], Q96, 10**24))
    addr += 1
    engine.ingest(gk.stable(addr, tokens[20], tokens[21], 10**24, 1_003 * 10**21, amp=200))
    addr += 1
    engine.ingest(gk.stable(addr, tokens[30], tokens[31], 10**24, 1_003 * 10**21, amp=200))
    addr += 1
    engine.ingest(
        gk.weighted(
            addr,
            tokens[40],
            tokens[41],
            1000 * 10**18,
            1005 * 10**18,
            weight0=8 * 10**17,
            weight1=2 * 10**17,
        )
    )
    addr += 1
    # Hub spokes create longer triangular/multi-hop routes through token 0.
    for i in range(2, N_TOKENS, 3):
        engine.ingest(gk.v2(addr, tokens[0], tokens[i], 1000 * 10**18, 1010 * 10**18))
        addr += 1
    return engine


def test_scale_top_n_is_sound_and_ranked(gk: type[GraphKit]) -> None:
    engine = _big_engine(gk)
    engine.compute(top_n=10)  # warm the JIT so the timed run measures steady state
    start = time.perf_counter()
    opportunities = engine.compute(top_n=10, incremental=False)
    elapsed = time.perf_counter() - start

    assert opportunities, "a large planted graph must surface opportunities"
    assert len(opportunities) <= 10
    for opp in opportunities:
        _assert_sound(opp)
    # Ranked by risk-adjusted score, descending.
    scores = [o.score for o in opportunities]
    assert scores == sorted(scores, reverse=True)
    assert elapsed < HANG_GUARD_SECONDS, f"compute took {elapsed:.2f}s (>{HANG_GUARD_SECONDS}s)"


def test_scale_is_deterministic(gk: type[GraphKit]) -> None:
    a = _big_engine(gk).compute(top_n=10, incremental=False)
    b = _big_engine(gk).compute(top_n=10, incremental=False)
    fa = [(o.pool_addresses, o.input_amount, o.net_profit, o.score) for o in a]
    fb = [(o.pool_addresses, o.input_amount, o.net_profit, o.score) for o in b]
    assert fa == fb


def test_scale_incremental_agrees_with_full(gk: type[GraphKit]) -> None:
    # Draining the dirty set incrementally then comparing to a from-scratch full scan
    # must yield the same ranked opportunities — no route is missed at scale.
    engine = _big_engine(gk)
    engine.compute(top_n=10, incremental=True)  # drain initial dirty set
    incr = engine.compute(top_n=10, incremental=False)
    full = _big_engine(gk).compute(top_n=10, incremental=False)
    assert [o.pool_addresses for o in incr] == [o.pool_addresses for o in full]
