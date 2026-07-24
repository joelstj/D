"""Unit tests for cycle structural helpers."""

from __future__ import annotations

import math

import pytest

from l2arb.detect import cycle as cyc
from l2arb.graph.rategraph import RateEdge

pytestmark = pytest.mark.unit

A = (42161, "0x" + "01" * 20)
B = (42161, "0x" + "02" * 20)
C = (42161, "0x" + "03" * 20)


def edge(src: tuple[int, str], dst: tuple[int, str], pool: str, rate: float) -> RateEdge:
    return RateEdge(src, dst, pool, rate, -math.log(rate))


def test_is_closed() -> None:
    good = [edge(A, B, "p1", 1.1), edge(B, A, "p2", 1.0)]
    assert cyc.is_closed(good)
    assert not cyc.is_closed([])
    # Broken chain: second edge does not start where the first ended.
    assert not cyc.is_closed([edge(A, B, "p1", 1.1), edge(C, A, "p2", 1.0)])
    # Not returning to start.
    assert not cyc.is_closed([edge(A, B, "p1", 1.1), edge(B, C, "p2", 1.0)])


def test_tokens_and_pools() -> None:
    tri = [edge(A, B, "p1", 1.0), edge(B, C, "p2", 1.0), edge(C, A, "p3", 1.1)]
    assert cyc.cycle_tokens(tri) == [A, B, C]
    assert cyc.cycle_pools(tri) == ["p1", "p2", "p3"]


def test_is_simple() -> None:
    tri = [edge(A, B, "p1", 1.0), edge(B, C, "p2", 1.0), edge(C, A, "p3", 1.1)]
    assert cyc.is_simple(tri)
    # Repeated pool.
    assert not cyc.is_simple([edge(A, B, "p1", 1.1), edge(B, A, "p1", 1.0)])
    # Repeated intermediate token (A visited twice as a node): A->B->A->B pattern.
    repeat = [edge(A, B, "p1", 1.0), edge(B, A, "p2", 1.0), edge(A, B, "p3", 1.1)]
    assert not cyc.is_simple(repeat)


def test_log_margin_sign() -> None:
    profitable = [edge(A, B, "p1", 1.1), edge(B, A, "p2", 1.0)]  # product 1.1 > 1
    assert cyc.cycle_log_margin(profitable) < 0
    losing = [edge(A, B, "p1", 0.9), edge(B, A, "p2", 1.0)]  # product 0.9 < 1
    assert cyc.cycle_log_margin(losing) > 0
    assert cyc.cycle_log_margin(profitable) == pytest.approx(-math.log(1.1))
