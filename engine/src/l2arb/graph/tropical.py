"""Tropical (min-plus) K-hop sweep — a bounded negative-cycle pre-filter.

In the min-plus semiring (``+`` -> ``min``, ``*`` -> ``+``) the ``k``-th matrix
power of the weight matrix gives the lightest closed walk of exactly ``k`` edges
(docs/ARBITRAGE_THEORY §3.3). Taking the diagonal minimum over ``k = 2..K`` tells
us, for every token, the lightest closed walk of length ``<= K`` through it. A
**negative** value flags a token that sits on a bounded negative cycle — i.e. a
multi-hop arbitrage no longer than ``K`` hops.

This is the periodic *full sweep*: it bounds hop count (which we want for gas and
latency) and is dense. It only **flags** roots; the multi-hop detector then
recovers each concrete cycle by a targeted bounded search. The inner min-plus
product is O(V^3) per power, O(V^2) memory. It is **``numba``-JIT-accelerated**
when numba is available (call :func:`warmup` once at startup to pay the one-time
compilation cost off the hot path); a pure-``numpy`` reference is the fallback and
the correctness oracle (a test asserts the two agree bit-for-bit).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

import numpy as np
from numpy.typing import NDArray

from l2arb.graph.rategraph import RateGraph
from l2arb.model.token import TokenKey

try:
    from numba import njit

    _HAS_NUMBA = True
except ImportError:  # pragma: no cover - numba is a hard dependency; fallback for stripped envs
    _HAS_NUMBA = False

__all__ = ["bounded_min_closed_walk", "negative_cycle_roots", "warmup"]

EPS = 1e-12


def _weight_matrix(graph: RateGraph) -> tuple[list[TokenKey], NDArray[np.float64]]:
    """Best-edge weight matrix ``W[i, j]`` (``inf`` where there is no edge)."""
    best = graph.best_edges()
    nodes: set[TokenKey] = set(best.keys())
    for row in best.values():
        nodes.update(row.keys())
    ordered = sorted(nodes)
    index = {node: i for i, node in enumerate(ordered)}
    n = len(ordered)
    w = np.full((n, n), np.inf, dtype=np.float64)
    for src, row in best.items():
        for dst, edge in row.items():
            w[index[src], index[dst]] = edge.log_weight
    return ordered, w


def _min_plus_matmul_numpy(x: NDArray[np.float64], y: NDArray[np.float64]) -> NDArray[np.float64]:
    """Min-plus product ``(X ⊗ Y)[i, j] = min_m X[i, m] + Y[m, j]`` (numpy reference).

    Loops over the contracted index ``m`` to keep memory at ``O(V^2)`` instead of
    materialising the ``O(V^3)`` intermediate. This is the correctness oracle.
    """
    n = x.shape[0]
    out = np.full((n, n), np.inf, dtype=np.float64)
    for m in range(n):
        np.minimum(out, x[:, m][:, None] + y[m][None, :], out=out)
    return out


# The active kernel — numba when available, numpy otherwise. Same signature/result.
_MatMul = Callable[[NDArray[np.float64], NDArray[np.float64]], NDArray[np.float64]]
_min_plus_matmul: _MatMul

if _HAS_NUMBA:

    @njit(cache=True, fastmath=False)  # pragma: no cover - JIT-compiled, not line-traceable
    def _min_plus_matmul_numba(
        x: NDArray[np.float64], y: NDArray[np.float64]
    ) -> NDArray[np.float64]:
        """numba-JIT min-plus product — same result as the numpy reference."""
        n = x.shape[0]
        out = np.full((n, n), np.inf, dtype=np.float64)
        for i in range(n):
            for j in range(n):
                best = np.inf
                for m in range(n):
                    candidate = x[i, m] + y[m, j]
                    if candidate < best:
                        best = candidate
                out[i, j] = best
        return out

    _min_plus_matmul = cast("_MatMul", _min_plus_matmul_numba)
else:  # pragma: no cover - numba is a hard dependency
    _min_plus_matmul = _min_plus_matmul_numpy


def warmup() -> None:
    """Trigger JIT compilation on a tiny input so the first real sweep is fast.

    Call once at process startup (the first ``numba`` call otherwise pays the
    compilation cost on the hot path — learnings.md ``[latency]``). No-op without
    numba.
    """
    if _HAS_NUMBA:  # pragma: no branch - numba is a hard dependency
        tiny = np.zeros((2, 2), dtype=np.float64)
        _min_plus_matmul(tiny, tiny)


def bounded_min_closed_walk(graph: RateGraph, max_hops: int) -> dict[TokenKey, float]:
    """Map each token to its lightest closed-walk weight over lengths ``2..max_hops``.

    A value ``< 0`` means the token lies on a negative cycle of at most
    ``max_hops`` hops. ``max_hops`` must be ``>= 2``.
    """
    if max_hops < 2:
        raise ValueError(f"max_hops must be >= 2, got {max_hops}")
    ordered, w = _weight_matrix(graph)
    n = len(ordered)
    if n == 0:
        return {}
    power = w.copy()  # exactly 1 edge
    best_closed = np.full(n, np.inf, dtype=np.float64)
    for _ in range(2, max_hops + 1):
        power = _min_plus_matmul(power, w)  # exactly k edges
        best_closed = np.minimum(best_closed, np.diagonal(power))
    return {node: float(best_closed[i]) for i, node in enumerate(ordered)}


def negative_cycle_roots(graph: RateGraph, max_hops: int) -> set[TokenKey]:
    """Tokens that sit on a negative cycle of length ``<= max_hops``."""
    return {
        node for node, weight in bounded_min_closed_walk(graph, max_hops).items() if weight < -EPS
    }
