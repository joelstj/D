"""Latency benchmark for the numba-accelerated tropical min-plus sweep."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from l2arb.graph import tropical
from l2arb.graph.tropical import warmup

pytestmark = pytest.mark.benchmark


def _matrix(n: int) -> np.ndarray:
    # Deterministic dense weight matrix with a few negative entries.
    return ((np.arange(n * n, dtype=np.float64) % 11) - 5).reshape(n, n)


def test_min_plus_matmul_numba_latency(benchmark: Any) -> None:
    warmup()  # pay JIT compilation once, off the measured path
    x = _matrix(80)
    # Correctness still holds at this size before we time it.
    assert np.array_equal(
        tropical._min_plus_matmul(x, x),
        tropical._min_plus_matmul_numpy(x, x),
    )
    result = benchmark(lambda: tropical._min_plus_matmul(x, x))
    assert result.shape == (80, 80)
