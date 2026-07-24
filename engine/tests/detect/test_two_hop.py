"""Unit tests for the spatial 2-hop detector, including no-false-positives."""

from __future__ import annotations

import pytest

from graphkit import GraphKit
from l2arb.detect.cycle import cycle_log_margin, cycle_pools, is_closed
from l2arb.detect.two_hop import two_hop_candidates

pytestmark = pytest.mark.unit


def test_finds_planted_two_hop(gk: type[GraphKit]) -> None:
    a, b = gk.token(1), gk.token(2)
    # P1 prices A/B ~1:1; P2 pays 1.1 B per A -> buy B in P2, sell in P1.
    p1 = gk.v2(10, a, b, 1000 * 10**18, 1000 * 10**18)
    p2 = gk.v2(11, a, b, 1000 * 10**18, 1100 * 10**18)
    graph = gk.graph([p1, p2])

    cands = two_hop_candidates(graph)
    assert cands, "expected a 2-hop candidate"
    for c in cands:
        assert is_closed(c)
        assert len(c) == 2
        assert cycle_log_margin(c) < 0  # margin-profitable
        assert len(set(cycle_pools(c))) == 2  # two distinct pools


def test_no_false_positive_on_equal_pools(gk: type[GraphKit]) -> None:
    a, b = gk.token(1), gk.token(2)
    # Identical pools -> only a fee-losing round trip exists.
    p1 = gk.v2(10, a, b, 1000 * 10**18, 1000 * 10**18)
    p2 = gk.v2(11, a, b, 1000 * 10**18, 1000 * 10**18)
    assert two_hop_candidates(gk.graph([p1, p2])) == []


def test_single_pool_has_no_two_hop(gk: type[GraphKit]) -> None:
    a, b = gk.token(1), gk.token(2)
    assert two_hop_candidates(gk.graph([gk.v2(10, a, b, 10**18, 11 * 10**17)])) == []


def test_sources_restricts_scan(gk: type[GraphKit]) -> None:
    a, b, c, d = gk.token(1), gk.token(2), gk.token(3), gk.token(4)
    ab1 = gk.v2(10, a, b, 1000 * 10**18, 1000 * 10**18)
    ab2 = gk.v2(11, a, b, 1000 * 10**18, 1100 * 10**18)
    cd1 = gk.v2(12, c, d, 1000 * 10**18, 1000 * 10**18)
    cd2 = gk.v2(13, c, d, 1000 * 10**18, 1100 * 10**18)
    graph = gk.graph([ab1, ab2, cd1, cd2])
    # Both pairs carry an arb; seeding only from {A} finds only the A-B one.
    only_ab = two_hop_candidates(graph, sources={a.key})
    pools_found = {p for c in only_ab for p in cycle_pools(c)}
    assert pools_found == {ab1.address, ab2.address}


def test_cross_dex_two_hop_stable_and_v2(gk: type[GraphKit]) -> None:
    # A Curve stable pool (~1:1) vs a V2 pool pricing the pair off 1:1 -> arb.
    a, b = gk.token(1, decimals=18), gk.token(2, decimals=18)
    stable = gk.stable(10, a, b, 10**24, 10**24, amp=200)  # ~1:1
    v2 = gk.v2(11, a, b, 1000 * 10**18, 1150 * 10**18)  # skewed
    cands = two_hop_candidates(gk.graph([stable, v2]))
    assert cands
    for c in cands:
        assert {stable.address, v2.address} == set(cycle_pools(c))


def test_cross_dex_two_hop_weighted_and_v2(gk: type[GraphKit]) -> None:
    # An 80/20 Balancer pool vs a 50/50 V2 pool -> different marginal prices.
    a, b = gk.token(1, decimals=18), gk.token(2, decimals=18)
    weighted = gk.weighted(10, a, b, 10**24, 10**24, weight0=8 * 10**17, weight1=2 * 10**17)
    v2 = gk.v2(11, a, b, 10**24, 10**24)  # 50/50, price ~1
    cands = two_hop_candidates(gk.graph([weighted, v2]))
    assert cands  # the weight skew creates a spatial spread


def test_cross_dex_two_hop_v2_and_v3(gk: type[GraphKit]) -> None:
    # One V2 pool + one V3 pool on the same pair (a cross-dex 2-hop).
    a, b = gk.token(1, decimals=18), gk.token(2, decimals=18)
    v2 = gk.v2(10, a, b, 1000 * 10**18, 1000 * 10**18)  # price ~1
    # V3 at price ~1.1 (sqrtP = sqrt(1.1) * 2**96) so the pair is mispriced.
    sqrtp = int((1.1**0.5) * gk.UNITY_SQRT)
    v3 = gk.v3(11, a, b, sqrtp, 10**21)
    cands = two_hop_candidates(gk.graph([v2, v3]))
    assert cands
    for c in cands:
        assert {v2.address, v3.address} == set(cycle_pools(c))
