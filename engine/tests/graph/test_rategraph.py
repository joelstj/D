"""Unit + property tests for the exchange-rate graph.

Pins the graph mechanics (in-place O(1) pool rewrite, dirty tracking, multigraph
collapse) and the load-bearing identity ``sum(-ln r) < 0  <=>  prod(r) > 1``
(T-0402) that makes "profitable cycle" equivalent to "negative-weight cycle".
"""

from __future__ import annotations

import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from l2arb.graph.rategraph import RateGraph
from l2arb.model.blockstamp import Blockstamp
from l2arb.model.pool import PoolKind, PoolState, V2Reserves
from l2arb.model.token import Token

pytestmark = pytest.mark.unit

CHAIN = 42161
BS = Blockstamp(chain_id=CHAIN, number=1, block_hash="0x" + "ab" * 32, timestamp=1)
A = Token(chain_id=CHAIN, address="0x" + "11" * 20, decimals=18, symbol="A")
B = Token(chain_id=CHAIN, address="0x" + "22" * 20, decimals=6, symbol="B")
C = Token(chain_id=CHAIN, address="0x" + "33" * 20, decimals=18, symbol="C")


def v2_pool(addr: str, t0: Token, t1: Token, r0: int, r1: int, fee: int = 3000) -> PoolState:
    return PoolState(
        address=addr,
        kind=PoolKind.CONSTANT_PRODUCT,
        token0=t0,
        token1=t1,
        fee_pips=fee,
        blockstamp=BS,
        v2=V2Reserves(reserve0=r0, reserve1=r1),
    )


# ------------------------------- mechanics -------------------------------- #
def test_upsert_adds_two_directed_edges_and_marks_dirty() -> None:
    g = RateGraph(CHAIN)
    touched = g.upsert_pool(v2_pool("0x" + "aa" * 20, A, B, 10**18, 3000 * 10**6))
    assert touched == {A.key, B.key}
    assert g.num_edges == 2
    assert {e.dst for e in g.out_edges(A.key)} == {B.key}
    assert {e.dst for e in g.out_edges(B.key)} == {A.key}
    assert g.pop_dirty() == {A.key, B.key}
    assert g.pop_dirty() == set()  # cleared


def test_edge_rate_is_decimal_adjusted_human_price() -> None:
    g = RateGraph(CHAIN)
    # 1 A (18dp) reserve vs 3000 B (6dp) reserve -> ~3000 B per A, fee 0.3 %.
    g.upsert_pool(v2_pool("0x" + "aa" * 20, A, B, 10**18, 3000 * 10**6))
    edge = g.edges_between(A.key, B.key)[0]
    assert edge.rate == pytest.approx(3000 * 0.997, rel=1e-6)
    # -ln identity holds on the stored weight.
    assert edge.log_weight == pytest.approx(-math.log(edge.rate))


def test_update_replaces_edges_in_place() -> None:
    g = RateGraph(CHAIN)
    addr = "0x" + "aa" * 20
    g.upsert_pool(v2_pool(addr, A, B, 10**18, 3000 * 10**6))
    old = g.edges_between(A.key, B.key)[0].rate
    g.upsert_pool(v2_pool(addr, A, B, 10**18, 4000 * 10**6))  # price moved
    assert g.num_edges == 2  # not duplicated
    assert g.edges_between(A.key, B.key)[0].rate != old


def test_parallel_pools_are_kept_as_multigraph() -> None:
    g = RateGraph(CHAIN)
    g.upsert_pool(v2_pool("0x" + "aa" * 20, A, B, 10**18, 3000 * 10**6))
    g.upsert_pool(v2_pool("0x" + "bb" * 20, A, B, 10**18, 3100 * 10**6))
    assert len(g.edges_between(A.key, B.key)) == 2
    # best_edges collapses to the single cheapest-weight edge per pair.
    best = g.best_edges()
    assert len(best[A.key]) == 1
    chosen = best[A.key][B.key]
    assert chosen.log_weight == min(e.log_weight for e in g.edges_between(A.key, B.key))


def test_untradable_pool_is_treated_as_removal() -> None:
    g = RateGraph(CHAIN)
    addr = "0x" + "aa" * 20
    g.upsert_pool(v2_pool(addr, A, B, 10**18, 3000 * 10**6))
    assert g.num_edges == 2
    g.upsert_pool(v2_pool(addr, A, B, 0, 3000 * 10**6))  # zero reserve -> untradable
    assert g.num_edges == 0
    assert g.out_edges(A.key) == []


def test_remove_pool() -> None:
    g = RateGraph(CHAIN)
    addr = "0x" + "aa" * 20
    g.upsert_pool(v2_pool(addr, A, B, 10**18, 3000 * 10**6))
    g.pop_dirty()
    assert g.remove_pool(addr) == {A.key, B.key}
    assert g.num_edges == 0
    assert g.num_tokens == 0
    assert g.remove_pool("0x" + "ff" * 20) == set()  # unknown pool is a no-op


def test_wrong_chain_pool_rejected() -> None:
    g = RateGraph(CHAIN)
    other = Token(chain_id=8453, address="0x" + "44" * 20, decimals=18)
    other2 = Token(chain_id=8453, address="0x" + "55" * 20, decimals=18)
    bs = Blockstamp(chain_id=8453, number=1, block_hash="0x" + "cd" * 32, timestamp=1)
    pool = PoolState(
        address="0x" + "aa" * 20,
        kind=PoolKind.CONSTANT_PRODUCT,
        token0=other,
        token1=other2,
        fee_pips=3000,
        blockstamp=bs,
        v2=V2Reserves(10**18, 10**18),
    )
    with pytest.raises(ValueError, match="graph chain"):
        g.upsert_pool(pool)


def test_pool_accessor_and_tokens() -> None:
    g = RateGraph(CHAIN)
    addr = "0x" + "aa" * 20
    p = v2_pool(addr, A, B, 10**18, 3000 * 10**6)
    g.upsert_pool(p)
    assert g.pool(addr) is p
    assert g.tokens() == {A.key, B.key}


def test_removal_keeps_parallel_edge_and_other_neighbours() -> None:
    g = RateGraph(CHAIN)
    g.upsert_pool(v2_pool("0x" + "aa" * 20, A, B, 10**18, 3000 * 10**6))
    g.upsert_pool(v2_pool("0x" + "bb" * 20, A, B, 10**18, 3100 * 10**6))  # parallel A-B
    g.upsert_pool(v2_pool("0x" + "cc" * 20, A, C, 10**18, 10**18))  # A also -> C
    g.remove_pool("0x" + "aa" * 20)
    # The parallel A->B pool survives (bucket not emptied) ...
    assert len(g.edges_between(A.key, B.key)) == 1
    # ... and A keeps its A->C neighbour (src row not deleted).
    assert A.key in g.tokens()
    assert {e.dst for e in g.out_edges(A.key)} == {B.key, C.key}


def test_removal_empties_bucket_but_keeps_source_row() -> None:
    g = RateGraph(CHAIN)
    g.upsert_pool(v2_pool("0x" + "aa" * 20, A, B, 10**18, 3000 * 10**6))  # only A-B pool
    g.upsert_pool(v2_pool("0x" + "cc" * 20, A, C, 10**18, 10**18))  # A also -> C
    g.remove_pool("0x" + "aa" * 20)
    assert g.edges_between(A.key, B.key) == []  # A->B bucket fully removed
    assert A.key in g.tokens()  # A keeps its A->C row
    assert B.key not in g.tokens()  # B had only B->A, so its row is gone


def test_reupsert_from_untradable_handles_missing_edge_bucket() -> None:
    g = RateGraph(CHAIN)
    addr = "0x" + "aa" * 20
    g.upsert_pool(v2_pool(addr, A, B, 0, 3000 * 10**6))  # untradable: no edges added
    assert g.num_edges == 0
    # Re-upsert tradable: the rewrite must tolerate there being no edge bucket yet.
    g.upsert_pool(v2_pool(addr, A, B, 10**18, 3000 * 10**6))
    assert g.num_edges == 2


def test_all_edges_and_num_tokens() -> None:
    g = RateGraph(CHAIN)
    g.upsert_pool(v2_pool("0x" + "aa" * 20, A, B, 10**18, 3000 * 10**6))
    g.upsert_pool(v2_pool("0x" + "bb" * 20, B, C, 3000 * 10**6, 10**18))
    assert len(g.all_edges()) == 4
    assert g.num_tokens == 3  # A, B, C all have outgoing edges


# ------------------------------- identity --------------------------------- #
@settings(max_examples=300, deadline=None)
@given(rates=st.lists(st.floats(min_value=0.01, max_value=100.0), min_size=2, max_size=5))
def test_negative_cycle_iff_profitable_product(rates: list[float]) -> None:
    # The core equivalence, on the exact quantities the graph stores.
    log_sum = sum(-math.log(r) for r in rates)
    product = math.prod(rates)
    if product > 1.0 + 1e-9:
        assert log_sum < 0
    elif product < 1.0 - 1e-9:
        assert log_sum > 0
