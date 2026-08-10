"""Unit tests for l2arb.run — service startup sequencing and cleanup.

Focus: a crash (or Ctrl-C, modelled here as any exception) partway through
starting the service sequence must stop every already-started service before
the error propagates, so a mid-startup failure never leaves an orphaned
engine/ingestion/dashboard process holding its port for the next `l2arb run`.
"""

from __future__ import annotations

import signal
import sys
import unittest
from pathlib import Path
from unittest import mock

# Make the launcher package importable when run from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from l2arb import run as run_mod  # noqa: E402
from l2arb.paths import Layout  # noqa: E402
from l2arb.state import ComponentReadiness  # noqa: E402


class FakeService:
    """Records start/stop calls; a given instance can be told to fail to start."""

    instances: list["FakeService"] = []

    def __init__(self, name, cmd, cwd, env, log_path, health_url=None):
        self.name = name
        self.started = False
        self.stopped = False
        self.fail_to_start = False
        FakeService.instances.append(self)

    def start(self) -> None:
        if self.fail_to_start:
            raise RuntimeError(f"{self.name} failed to start")
        self.started = True

    def stop(self, grace: float = 6.0) -> None:
        self.stopped = True


class FakeHealthMonitor:
    """Records the services it was handed instead of running the real HUD loop."""

    last_services: list[FakeService] | None = None

    def __init__(self, services):
        FakeHealthMonitor.last_services = list(services)

    def run(self) -> int:
        return 0


class RunServiceCleanupTest(unittest.TestCase):
    def setUp(self):
        FakeService.instances = []
        FakeHealthMonitor.last_services = None
        self.lo = Layout(Path("/tmp/l2arb-test-run-ws"))

        patches = [
            mock.patch.object(run_mod, "Service", FakeService),
            mock.patch.object(run_mod, "HealthMonitor", FakeHealthMonitor),
            mock.patch.object(run_mod, "wait_http", lambda *a, **k: True),
            mock.patch.object(run_mod.webbrowser, "open", lambda *a, **k: None),
            mock.patch.object(
                run_mod.state,
                "probe",
                lambda lo: ComponentReadiness(engine=True, dashboard=True, ingestion=True),
            ),
            mock.patch.object(run_mod.config, "config_is_live_ready", lambda lo: True),
            mock.patch.object(run_mod.config, "engine_cmd", lambda lo: ["engine"]),
            mock.patch.object(run_mod.config, "ingestion_cmd", lambda lo: ["ingestion"]),
            mock.patch.object(run_mod.config, "dashboard_cmd", lambda lo: ["dashboard"]),
            mock.patch.object(run_mod.config, "dashboard_env", lambda lo, *, live, port: {}),
            mock.patch.object(run_mod.config, "health_url", lambda name, port: None),
            mock.patch.object(run_mod, "console", mock.MagicMock()),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_happy_path_hands_every_started_service_to_the_health_monitor(self):
        code = run_mod.run(self.lo, live=True, port=8787, open_browser=False)

        self.assertEqual(code, 0)
        self.assertEqual(len(FakeService.instances), 3)  # engine, ingestion, dashboard
        self.assertTrue(all(s.started for s in FakeService.instances))
        self.assertTrue(all(not s.stopped for s in FakeService.instances))
        self.assertEqual(FakeHealthMonitor.last_services, FakeService.instances)

    def test_ingestion_failing_to_start_stops_the_already_started_engine(self):
        # Fail the SECOND service (ingestion); the FIRST (engine) already started
        # and must be stopped before the exception propagates — not left running.
        original_init = FakeService.__init__

        def init_with_second_failing(self, name, *a, **k):
            original_init(self, name, *a, **k)
            if name == "ingestion":
                self.fail_to_start = True

        with mock.patch.object(FakeService, "__init__", init_with_second_failing):
            with self.assertRaises(RuntimeError):
                run_mod.run(self.lo, live=True, port=8787, open_browser=False)

        engine = next(s for s in FakeService.instances if s.name == "engine")
        ingestion = next(s for s in FakeService.instances if s.name == "ingestion")
        self.assertTrue(engine.started)
        self.assertTrue(engine.stopped, "the already-started engine must be stopped, not orphaned")
        self.assertFalse(ingestion.started)
        # The health monitor must never take over a partially-started stack.
        self.assertIsNone(FakeHealthMonitor.last_services)

    def test_dashboard_failing_to_start_stops_engine_and_ingestion(self):
        original_init = FakeService.__init__

        def init_with_dashboard_failing(self, name, *a, **k):
            original_init(self, name, *a, **k)
            if name == "dashboard":
                self.fail_to_start = True

        with mock.patch.object(FakeService, "__init__", init_with_dashboard_failing):
            with self.assertRaises(RuntimeError):
                run_mod.run(self.lo, live=True, port=8787, open_browser=False)

        for name in ("engine", "ingestion"):
            svc = next(s for s in FakeService.instances if s.name == name)
            self.assertTrue(svc.started)
            self.assertTrue(svc.stopped, f"{name} must be stopped, not orphaned")
        self.assertIsNone(FakeHealthMonitor.last_services)

    def test_keyboard_interrupt_mid_startup_also_stops_already_started_services(self):
        # Ctrl-C during wait_http's polling loop is a KeyboardInterrupt, not an
        # Exception — the cleanup must catch that too (BaseException), not just
        # ordinary exceptions.
        def interrupting_wait_http(*a, **k):
            raise KeyboardInterrupt()

        with mock.patch.object(run_mod, "wait_http", interrupting_wait_http):
            with self.assertRaises(KeyboardInterrupt):
                run_mod.run(self.lo, live=True, port=8787, open_browser=False)

        engine = next(s for s in FakeService.instances if s.name == "engine")
        self.assertTrue(engine.started)
        self.assertTrue(engine.stopped, "Ctrl-C mid-startup must not orphan the engine process")


class SigtermHandlerTest(unittest.TestCase):
    """`kill`/`docker stop` send SIGTERM, not SIGINT — Python installs a raising
    handler for SIGINT by default but leaves SIGTERM at the OS default (immediate
    terminate), which would skip run()'s cleanup entirely and orphan the child
    services (they live in their own process group and don't get the launcher's
    SIGTERM). `_install_sigterm_handler` closes that gap by routing SIGTERM
    through the same graceful KeyboardInterrupt path as Ctrl-C. Previously
    unexercised by any test (only reachable via an actual signal delivery) —
    covered directly here so a future refactor can't silently drop it.
    """

    def setUp(self):
        # A process signal handler is global state; always restore whatever was
        # registered before this test touched it, pass or fail.
        self._original = signal.getsignal(signal.SIGTERM)
        self.addCleanup(signal.signal, signal.SIGTERM, self._original)

    def test_on_sigterm_raises_keyboard_interrupt(self):
        with self.assertRaises(KeyboardInterrupt):
            run_mod._on_sigterm(signal.SIGTERM, None)

    def test_install_wires_sigterm_to_on_sigterm(self):
        run_mod._install_sigterm_handler()
        self.assertIs(signal.getsignal(signal.SIGTERM), run_mod._on_sigterm)

    def test_a_delivered_sigterm_is_caught_as_keyboard_interrupt(self):
        # End-to-end within this process: install the handler, then actually
        # raise SIGTERM against ourselves and confirm it surfaces as the same
        # KeyboardInterrupt run()'s cleanup already knows how to handle.
        import os

        run_mod._install_sigterm_handler()
        with self.assertRaises(KeyboardInterrupt):
            os.kill(os.getpid(), signal.SIGTERM)


if __name__ == "__main__":
    unittest.main()
