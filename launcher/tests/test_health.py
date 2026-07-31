"""Unit tests for the continuous health monitor / self-fix policy (no network).

These are pure-logic tests: the state machine, backoff, diagnostics, and HUD
renderer are all driven with an injected clock and a fake prober, so they are
deterministic and fast. The real-subprocess self-heal loop is covered
separately in ``test_health_integration.py``.

Run with:  python -m unittest discover -s launcher/tests
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

# Make the launcher package importable when run from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from l2arb.health import (  # noqa: E402
    Action,
    HealthMonitor,
    MonitorPolicy,
    Phase,
    Probe,
    ServiceHealth,
    backoff_delay,
    diagnose,
    render_hud,
)

POLICY = MonitorPolicy(
    startup_grace=30.0,
    degraded_restart_after=15.0,
    max_restarts=3,
    backoff_base=1.0,
    backoff_cap=8.0,
    stable_reset_after=60.0,
    probe_timeout=2.0,
    interval=1.0,
)


def started(*, name: str = "svc", has_endpoint: bool = True, now: float = 1000.0) -> ServiceHealth:
    sh = ServiceHealth(name=name, has_endpoint=has_endpoint)
    sh.mark_started(now)
    return sh


# --------------------------------------------------------------------------- #
# Fakes for the driver tests                                                   #
# --------------------------------------------------------------------------- #
class FakeClock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def time(self) -> float:
        return self.t

    def sleep(self, dt: float) -> None:
        self.t += dt


class FakeService:
    """Duck-typed stand-in for proc.Service the monitor can drive."""

    def __init__(
        self,
        name: str,
        *,
        alive: bool = True,
        responsive: bool | None = None,
        health_url: str | None = None,
        heals: bool = True,
    ) -> None:
        self.name = name
        self.health_url = health_url
        self._alive = alive
        self._responsive = responsive
        self.heals = heals
        self.pid = 4321
        self.restart_calls = 0
        self.stop_calls = 0

    def is_alive(self) -> bool:
        return self._alive

    def returncode(self) -> int | None:
        return None if self._alive else 7

    def tail_log(self, n: int = 3) -> list[str]:
        return [] if self._alive else ["boom: panicked"]

    def restart(self) -> None:
        self.restart_calls += 1
        self._alive = self.heals  # a proc that "won't heal" stays dead

    def stop(self) -> None:
        self.stop_calls += 1
        self._alive = False


def fake_prober(svc: FakeService) -> Probe:
    if not svc.is_alive():
        return Probe(alive=False, responsive=None, detail="process exited (code 7)")
    if svc.health_url is None:
        return Probe(alive=True, responsive=None, detail="alive (no health endpoint)")
    return Probe(alive=True, responsive=svc._responsive, latency_ms=2.0, detail="probed")


class SilentOut:
    def ok(self, *a: object) -> None: ...
    def warn(self, *a: object) -> None: ...
    def err(self, *a: object) -> None: ...
    def info(self, *a: object) -> None: ...
    def step(self, *a: object) -> None: ...
    def banner(self, *a: object) -> None: ...


def _monitor(services, policy=POLICY, clock=None, sleep=None):
    clock = clock or (lambda: 1000.0)
    sleep = sleep or (lambda _d: None)
    return HealthMonitor(
        services,
        policy,
        prober=fake_prober,
        clock=clock,
        sleep=sleep,
        out=SilentOut(),
        is_tty=False,
    )


# --------------------------------------------------------------------------- #
# backoff                                                                      #
# --------------------------------------------------------------------------- #
class BackoffTest(unittest.TestCase):
    def test_exponential_then_capped(self):
        self.assertEqual(backoff_delay(0, POLICY), 1.0)
        self.assertEqual(backoff_delay(1, POLICY), 2.0)
        self.assertEqual(backoff_delay(2, POLICY), 4.0)
        self.assertEqual(backoff_delay(3, POLICY), 8.0)
        self.assertEqual(backoff_delay(4, POLICY), 8.0)  # capped
        self.assertEqual(backoff_delay(9, POLICY), 8.0)

    def test_monotonic_nonnegative(self):
        prev = -1.0
        for a in range(6):
            d = backoff_delay(a, POLICY)
            self.assertGreaterEqual(d, 0.0)
            self.assertGreaterEqual(d, prev)
            prev = d


# --------------------------------------------------------------------------- #
# ServiceHealth.evaluate — the self-fix state machine                          #
# --------------------------------------------------------------------------- #
class EvaluateTest(unittest.TestCase):
    def test_alive_and_responsive_is_healthy(self):
        sh = started(now=1000)
        a = sh.evaluate(Probe(alive=True, responsive=True), 1000, POLICY)
        self.assertEqual(a, Action.NONE)
        self.assertEqual(sh.phase, Phase.HEALTHY)
        self.assertTrue(sh.ever_healthy)

    def test_alive_without_endpoint_is_healthy(self):
        sh = started(has_endpoint=False, now=1000)
        a = sh.evaluate(Probe(alive=True, responsive=None), 1000, POLICY)
        self.assertEqual(a, Action.NONE)
        self.assertEqual(sh.phase, Phase.HEALTHY)

    def test_dead_process_requests_restart(self):
        sh = started(now=1000)
        a = sh.evaluate(Probe(alive=False, responsive=None, detail="exited"), 1000, POLICY)
        self.assertEqual(a, Action.RESTART)
        self.assertEqual(sh.phase, Phase.DOWN)

    def test_unresponsive_within_startup_grace_is_patient(self):
        sh = started(now=1000)  # never healthy yet
        a = sh.evaluate(Probe(alive=True, responsive=False), 1010, POLICY)  # 10s < 30s grace
        self.assertEqual(a, Action.NONE)
        self.assertEqual(sh.phase, Phase.STARTING)

    def test_unresponsive_after_grace_degrades_then_restarts(self):
        sh = started(now=1000)
        sh.ever_healthy = True  # already booted once → no startup grace
        a = sh.evaluate(Probe(alive=True, responsive=False), 1000, POLICY)
        self.assertEqual(sh.phase, Phase.DEGRADED)
        self.assertEqual(a, Action.NONE)  # 0s < 15s degraded window
        a = sh.evaluate(Probe(alive=True, responsive=False), 1016, POLICY)  # 16s >= 15s
        self.assertEqual(a, Action.RESTART)

    def test_restart_restores_startup_grace(self):
        # Regression: mark_started must re-arm the startup grace (reset
        # ever_healthy). A service that was healthy, then restarted, then boots
        # slowly must get the full startup_grace again — not the much shorter
        # degraded window — or it can be re-restarted mid-boot into a crash-loop.
        sh = started(now=1000)
        sh.evaluate(Probe(alive=True, responsive=True), 1000, POLICY)  # becomes healthy
        self.assertTrue(sh.ever_healthy)
        sh.mark_started(now=2000)  # a restart
        self.assertFalse(sh.ever_healthy)  # grace re-armed for the new instance
        # Unresponsive 10s into the reboot (< 30s grace) is patient, not degraded.
        a = sh.evaluate(Probe(alive=True, responsive=False), 2010, POLICY)
        self.assertEqual(a, Action.NONE)
        self.assertEqual(sh.phase, Phase.STARTING)

    def test_restart_budget_exhausted_gives_up_and_is_sticky(self):
        sh = started(now=1000)
        sh.restarts = POLICY.max_restarts  # already spent the budget
        a = sh.evaluate(Probe(alive=False, responsive=None), 2000, POLICY)
        self.assertEqual(a, Action.GIVE_UP)
        self.assertEqual(sh.phase, Phase.FAILED)
        # FAILED is terminal: no further churn.
        a2 = sh.evaluate(Probe(alive=False, responsive=None), 2001, POLICY)
        self.assertEqual(a2, Action.NONE)
        self.assertEqual(sh.phase, Phase.FAILED)

    def test_backoff_gates_the_next_restart(self):
        sh = started(now=1000)
        sh.restarts = 1
        sh.last_restart_at = 1000.0  # backoff_delay(1) == 2.0
        a = sh.evaluate(Probe(alive=False, responsive=None), 1001, POLICY)  # only 1s elapsed
        self.assertEqual(a, Action.NONE)
        a = sh.evaluate(Probe(alive=False, responsive=None), 1003, POLICY)  # 3s >= 2s
        self.assertEqual(a, Action.RESTART)

    def test_sustained_health_resets_the_restart_budget(self):
        sh = started(now=1000)
        sh.restarts = 2
        sh.evaluate(Probe(alive=True, responsive=True), 1000, POLICY)
        self.assertEqual(sh.restarts, 2)  # healthy_since just set
        sh.evaluate(Probe(alive=True, responsive=True), 1060, POLICY)  # 60s >= stable_reset
        self.assertEqual(sh.restarts, 0)


# --------------------------------------------------------------------------- #
# diagnose                                                                     #
# --------------------------------------------------------------------------- #
class DiagnoseTest(unittest.TestCase):
    def test_dead_includes_exit_code_and_log_tail(self):
        d = diagnose(alive=False, returncode=1, responsive=None, log_tail=["oops", "bad"])
        self.assertIn("exited", d)
        self.assertIn("1", d)
        self.assertIn("oops", d)

    def test_unresponsive_endpoint(self):
        d = diagnose(alive=True, returncode=None, responsive=False, log_tail=[])
        self.assertIn("not responding", d)

    def test_no_endpoint(self):
        d = diagnose(alive=True, returncode=None, responsive=None, log_tail=[])
        self.assertIn("no health endpoint", d)


# --------------------------------------------------------------------------- #
# render_hud                                                                   #
# --------------------------------------------------------------------------- #
class RenderHudTest(unittest.TestCase):
    def _healths(self):
        ok = started(name="engine", now=1000)
        ok.evaluate(Probe(alive=True, responsive=True, latency_ms=2.0), 1000, POLICY)
        ok.pid = 123
        bad = started(name="ingestion", now=1000)
        bad.phase = Phase.FAILED
        bad.restarts = 3
        return [ok, bad]

    def test_plain_has_names_states_and_counts(self):
        txt = render_hud(self._healths(), 1005.0, 1000.0, use_color=False)
        self.assertIn("engine", txt)
        self.assertIn("ingestion", txt)
        self.assertIn("healthy", txt)
        self.assertIn("failed", txt)
        self.assertIn("3", txt)  # restart count of the failed service
        self.assertNotIn("\033[", txt)  # no ANSI when color disabled

    def test_color_emits_ansi(self):
        txt = render_hud(self._healths(), 1005.0, 1000.0, use_color=True)
        self.assertIn("\033[", txt)


# --------------------------------------------------------------------------- #
# HealthMonitor driver                                                         #
# --------------------------------------------------------------------------- #
class MonitorTest(unittest.TestCase):
    def test_healthy_service_is_left_alone(self):
        svc = FakeService("dash", alive=True, responsive=True, health_url="http://x/health")
        m = _monitor([svc])
        acts = m.tick(now=1000.0)
        self.assertEqual(acts["dash"], Action.NONE)
        self.assertEqual(svc.restart_calls, 0)
        self.assertEqual(m.health["dash"].phase, Phase.HEALTHY)

    def test_dead_service_is_restarted(self):
        svc = FakeService("engine", alive=False, heals=True)
        m = _monitor([svc])
        acts = m.tick(now=1000.0)
        self.assertEqual(acts["engine"], Action.RESTART)
        self.assertEqual(svc.restart_calls, 1)
        self.assertEqual(m.health["engine"].restarts, 1)

    def test_backoff_then_giveup_and_exit_nonzero(self):
        fc = FakeClock(1000.0)
        svc = FakeService("x", alive=False, heals=False)  # never comes back
        policy = MonitorPolicy(
            max_restarts=2, backoff_base=1.0, backoff_cap=4.0,
            degraded_restart_after=15.0, startup_grace=30.0,
            stable_reset_after=60.0, interval=1.0,
        )
        m = _monitor([svc], policy=policy, clock=fc.time, sleep=fc.sleep)
        rc = m.run()
        self.assertEqual(rc, 1)  # all services terminal → failure exit
        self.assertEqual(svc.restart_calls, 2)  # exactly the budget
        self.assertEqual(m.health["x"].phase, Phase.FAILED)
        self.assertGreaterEqual(svc.stop_calls, 1)  # stopped on give-up / teardown

    def test_recovery_after_a_restart(self):
        # Dead on the first probe, then heals on restart → back to HEALTHY.
        svc = FakeService("engine", alive=False, heals=True, health_url="http://x/health")
        svc._responsive = True
        m = _monitor([svc])
        m.tick(now=1000.0)  # dead → restart (svc now alive+responsive)
        acts = m.tick(now=1002.0)  # next probe sees it healthy
        self.assertEqual(acts["engine"], Action.NONE)
        self.assertEqual(m.health["engine"].phase, Phase.HEALTHY)
        self.assertEqual(svc.restart_calls, 1)


if __name__ == "__main__":
    unittest.main()
