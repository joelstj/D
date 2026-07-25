"""Unit tests for the stage-latency stopwatch (deterministic, injected clock)."""

from __future__ import annotations

import pytest

from l2arb.obs.latency import COMPONENT, Stopwatch

pytestmark = pytest.mark.unit


class _FakeClock:
    """A controllable monotonic nanosecond clock for deterministic timing."""

    def __init__(self) -> None:
        self.now = 0

    def __call__(self) -> int:
        return self.now

    def advance_ms(self, ms: float) -> None:
        self.now += int(ms * 1_000_000)


def test_stage_records_elapsed_ms() -> None:
    clock = _FakeClock()
    sw = Stopwatch(clock_ns=clock)
    with sw.stage("build"):
        clock.advance_ms(2.5)
    d = sw.to_dict()
    assert d["component"] == COMPONENT == "engine"
    assert d["stages"] == [{"stage": "build", "ms": 2.5}]
    assert d["total_ms"] == 2.5


def test_multiple_stages_accumulate_in_order() -> None:
    clock = _FakeClock()
    sw = Stopwatch(clock_ns=clock)
    for name, ms in (("build", 1.0), ("detect", 3.0), ("rank", 0.5)):
        with sw.stage(name):
            clock.advance_ms(ms)
    d = sw.to_dict()
    assert [s["stage"] for s in d["stages"]] == ["build", "detect", "rank"]
    assert [s["ms"] for s in d["stages"]] == [1.0, 3.0, 0.5]
    assert d["total_ms"] == 4.5


def test_record_appends_premeasured_stage() -> None:
    sw = Stopwatch(clock_ns=_FakeClock())
    sw.record("engine_roundtrip", 8.25)
    assert sw.to_dict()["stages"] == [{"stage": "engine_roundtrip", "ms": 8.25}]


def test_stage_records_even_on_exception() -> None:
    clock = _FakeClock()
    sw = Stopwatch(clock_ns=clock)

    def _boom() -> None:
        with sw.stage("detect"):
            clock.advance_ms(1.0)
            raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        _boom()
    # The stage duration is still captured (finally block) so a failing request is
    # still attributable — instrumentation must not hide where time went.
    assert sw.to_dict()["stages"] == [{"stage": "detect", "ms": 1.0}]


def test_default_clock_is_monotonic_and_nonnegative() -> None:
    sw = Stopwatch()
    with sw.stage("noop"):
        pass
    d = sw.to_dict()
    assert d["stages"][0]["stage"] == "noop"
    assert d["stages"][0]["ms"] >= 0.0
    assert d["total_ms"] >= 0.0


def test_rounding_to_four_decimals() -> None:
    clock = _FakeClock()
    sw = Stopwatch(clock_ns=clock)
    with sw.stage("s"):
        clock.now += 123_456  # 0.123456 ms
    assert sw.to_dict()["stages"][0]["ms"] == 0.1235
