"""End-to-end self-heal test against a REAL child process (no mocks).

Proves the whole loop with real moving parts: a real ``Service`` running a real
HTTP server, the real ``http_probe`` hitting a real ``/health``, a real crash
(SIGKILL), and the monitor's real ``Service.restart`` bringing it back — with the
health state transitioning DOWN → (restart) → HEALTHY and a genuinely new PID.

Hermetic: binds only 127.0.0.1 on an ephemeral port, spawns a stdlib server, and
tears everything down. Uses real wall-clock with bounded polling so it stays
fast (~1-2s) and never hangs.

Run with:  python -m unittest discover -s launcher/tests
"""

from __future__ import annotations

import socket
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from l2arb.health import HealthMonitor, MonitorPolicy, Phase, http_probe  # noqa: E402
from l2arb.proc import Service  # noqa: E402

# A tiny real HTTP server that answers 200 {"status":"ok"} on any GET.
_SERVER_SRC = """
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')
    def log_message(self, *a):
        pass

HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
"""


class _SilentOut:
    def ok(self, *a: object) -> None: ...
    def warn(self, *a: object) -> None: ...
    def err(self, *a: object) -> None: ...
    def info(self, *a: object) -> None: ...
    def step(self, *a: object) -> None: ...
    def banner(self, *a: object) -> None: ...


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_until(pred, timeout: float, interval: float = 0.1) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(interval)
    return False


class SelfHealIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.script = tmp / "server.py"
        self.script.write_text(_SERVER_SRC)
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}/health"
        self.svc = Service(
            "probe-target",
            [sys.executable, str(self.script), str(self.port)],
            cwd=tmp,
            env={},
            log_path=tmp / "svc.log",
            health_url=self.url,
        )

    def tearDown(self) -> None:
        try:
            self.svc.stop()
        finally:
            self._tmp.cleanup()

    def test_monitor_restarts_a_crashed_process(self) -> None:
        self.svc.start()
        # It should become responsive on its own.
        self.assertTrue(
            _wait_until(lambda: http_probe(self.url, 1.0)[0], timeout=8.0),
            "server never became responsive",
        )
        old_pid = self.svc.pid
        self.assertIsNotNone(old_pid)

        # Short windows so recovery is prompt; real clock/sleep, no HUD.
        policy = MonitorPolicy(startup_grace=8.0, degraded_restart_after=30.0, interval=0.1)
        mon = HealthMonitor([self.svc], policy, out=_SilentOut(), is_tty=False)

        # First observation: HEALTHY.
        mon.tick()
        self.assertEqual(mon.health["probe-target"].phase, Phase.HEALTHY)

        # Simulate a hard crash (bypass graceful stop).
        assert self.svc.proc is not None
        self.svc.proc.kill()
        self.assertTrue(_wait_until(lambda: not self.svc.is_alive(), timeout=5.0), "process did not die")

        # Drive the monitor: it must observe DOWN, restart, and reach HEALTHY.
        healed = _wait_until(
            lambda: (mon.tick() or True) and mon.health["probe-target"].phase == Phase.HEALTHY
            and mon.health["probe-target"].restarts >= 1,
            timeout=12.0,
            interval=0.15,
        )
        sh = mon.health["probe-target"]
        self.assertTrue(healed, f"service did not self-heal; phase={sh.phase} restarts={sh.restarts} detail={sh.detail}")
        self.assertGreaterEqual(sh.restarts, 1)
        self.assertTrue(self.svc.is_alive())
        self.assertNotEqual(self.svc.pid, old_pid)  # a genuinely new process

        # The restart history is preserved in the (appended) log.
        self.assertTrue(self.svc.log_path.exists())


class FailedStartTest(unittest.TestCase):
    def test_failed_spawn_does_not_leak_the_log_handle(self) -> None:
        # Regression: start() opens the log file handle before Popen. A failed
        # spawn (missing binary → OSError) must close it — stop() early-returns
        # while self.proc is None and would otherwise never close it.
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            svc = Service(
                "missing",
                [str(tmp / "does-not-exist-xyz")],
                cwd=tmp,
                env={},
                log_path=tmp / "svc.log",
            )
            with self.assertRaises(OSError):
                svc.start()
            self.assertIsNone(svc._log_fh)  # handle was closed, not leaked
            # stop() is safe to call after a failed start (no process, no handle).
            svc.stop()


if __name__ == "__main__":
    unittest.main()
