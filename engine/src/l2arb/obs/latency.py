"""Stage-latency measurement for the ``/detect`` request path (observability only).

A :class:`Stopwatch` records how long each stage of a detection request takes —
``build`` (assemble the per-chain rate graphs) → ``detect`` (run every detector) →
``rank`` (net-price, de-duplicate, top-N) → ``serialize`` (emit JSON) — and folds
them into a compact ``timing`` block that rides back in the response. The
ingestion → dashboard latency-health pipeline (root ``CLAUDE.md``) reads that block
to attribute end-to-end latency to the engine's internal stages, so a bottleneck
inside the engine is visible in the dashboard rather than hidden inside one opaque
"engine round-trip" number.

This is pure instrumentation: it measures elapsed **monotonic** time, never touches
market data, holds no keys, and cannot change a detection result. The clock is
**injectable** (default :func:`time.perf_counter_ns`) so tests are deterministic;
wall-clock is never read here — the single-host end-to-end anchor is stamped once by
the ingestion layer, not by the engine.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

__all__ = ["COMPONENT", "Stopwatch"]

#: Component label stamped into every engine timing block.
COMPONENT = "engine"

_NS_PER_MS = 1_000_000.0


@dataclass
class Stopwatch:
    """Accumulate named stage durations using an injectable monotonic clock.

    ``clock_ns`` must return a monotonically non-decreasing nanosecond counter
    (default :func:`time.perf_counter_ns`). Durations are reported in milliseconds,
    rounded to four decimals (sub-microsecond) in :meth:`to_dict`.
    """

    clock_ns: Callable[[], int] = time.perf_counter_ns
    _stages: list[tuple[str, float]] = field(default_factory=list)

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        """Time the wrapped block and append ``(name, elapsed_ms)``.

        The duration is recorded in a ``finally`` block, so a stage that raises is
        still attributed — a failing request must not hide where its time went.
        """
        start = self.clock_ns()
        try:
            yield
        finally:
            self.record(name, (self.clock_ns() - start) / _NS_PER_MS)

    def record(self, name: str, ms: float) -> None:
        """Append a pre-measured stage duration (milliseconds)."""
        self._stages.append((name, ms))

    @property
    def total_ms(self) -> float:
        """Sum of all recorded stage durations (milliseconds)."""
        return sum((ms for _, ms in self._stages), 0.0)

    def to_dict(self) -> dict[str, Any]:
        """Render the compact timing block relayed in the detection response."""
        return {
            "component": COMPONENT,
            "stages": [{"stage": name, "ms": round(ms, 4)} for name, ms in self._stages],
            "total_ms": round(self.total_ms, 4),
        }
