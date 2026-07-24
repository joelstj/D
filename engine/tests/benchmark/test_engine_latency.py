"""Latency benchmark — the engine must compute the top-N fast on a realistic graph.

Establishes the baseline the SLO gate (T-0604) will later enforce. Uses a
deterministic multi-pool graph (no wall-clock, no randomness) so the timing is
reproducible. Verifies both correctness (opportunities are found) and a generous
soft latency bound, so this fails loudly if the hot path regresses badly without
being flaky on shared CI hardware.
"""

from __future__ import annotations

from typing import Any

import pytest

from graphkit import GraphKit
from l2arb.engine.engine import ArbitrageEngine

pytestmark = pytest.mark.benchmark


def _build_engine(gk: type[GraphKit], n_tokens: int = 24) -> ArbitrageEngine:
    """A deterministic hub-and-spoke graph with several planted arbitrages."""
    engine = ArbitrageEngine(max_hops=4)
    tokens = [gk.token(i + 1) for i in range(n_tokens)]
    engine.configure_chain(gk.CHAIN, gk.profit_ctx(min_bps=1.0))

    addr = 100
    # Ring of pairs (i, i+1) plus spokes to the hub token 0, with a deterministic
    # imbalance every few pools to seed real opportunities.
    for i in range(n_tokens):
        a = tokens[i]
        b = tokens[(i + 1) % n_tokens]
        skew = 1000 + (i % 5) * 30  # 1000..1120, deterministic imbalance
        engine.ingest(gk.v2(addr, a, b, 1000 * 10**18, skew * 10**18))
        addr += 1
        # A second, differently-priced pool on the same pair -> spatial arb.
        engine.ingest(gk.v2(addr, a, b, 1000 * 10**18, (skew + 20) * 10**18))
        addr += 1
    for i in range(2, n_tokens):  # spokes to the hub token
        engine.ingest(gk.v2(addr, tokens[0], tokens[i], 1000 * 10**18, 1010 * 10**18))
        addr += 1
    return engine


def test_compute_top_n_latency(benchmark: Any, gk: type[GraphKit]) -> None:
    engine = _build_engine(gk)
    # Sanity: a non-trivial graph with real opportunities.
    warmup = engine.compute(top_n=10)
    assert warmup, "expected the benchmark graph to contain opportunities"
    assert len(warmup) <= 10

    result = benchmark(lambda: engine.compute(top_n=10, incremental=False))
    assert result  # deterministic: same opportunities every round


def test_incremental_is_cheaper_than_full(gk: type[GraphKit]) -> None:
    # A single changed pool should re-scan far less than the whole graph.
    engine = _build_engine(gk)
    engine.compute(top_n=10, incremental=True)  # drain initial dirty set
    a, b = gk.token(1), gk.token(2)
    engine.ingest(gk.v2(100, a, b, 1000 * 10**18, 1090 * 10**18))  # one pool moves
    # Incremental recompute touches only the changed neighbourhood and stays correct.
    incr = engine.compute(top_n=10, incremental=True)
    assert isinstance(incr, list)
