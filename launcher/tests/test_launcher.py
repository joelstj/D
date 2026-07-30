"""Unit tests for the launcher's pure logic (no builds, no network).

Run with:  python -m unittest discover -s launcher/tests
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# Make the launcher package importable when run from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from l2arb import cli, config, console, prereqs, proc, state  # noqa: E402
from l2arb.paths import Layout, workspace_root  # noqa: E402
from l2arb.prereqs import _VERSION_RE, _ge, merge_path  # noqa: E402
from l2arb.proc import _resolve  # noqa: E402


class PathsTest(unittest.TestCase):
    def test_workspace_root_env_override(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ["L2ARB_HOME"] = d
            try:
                self.assertEqual(workspace_root(), Path(d).resolve())
            finally:
                del os.environ["L2ARB_HOME"]

    def test_layout_component_paths(self):
        lo = Layout(Path("/tmp/ws"))
        self.assertEqual(lo.engine, Path("/tmp/ws/engine"))
        self.assertEqual(lo.dashboard_backend_entry, Path("/tmp/ws/dashboard/backend/dist/index.js"))
        self.assertTrue(str(lo.ingest_binary).endswith("l2-ingest") or str(lo.ingest_binary).endswith("l2-ingest.exe"))


class StateTest(unittest.TestCase):
    def test_probe_empty_workspace_is_not_ready(self):
        with tempfile.TemporaryDirectory() as d:
            r = state.probe(Layout(Path(d)))
            self.assertFalse(r.dashboard)
            self.assertFalse(r.engine)
            self.assertFalse(r.ingestion)
            self.assertFalse(r.full)

    def test_next_step_guides_from_nothing_to_live(self):
        none = state.ComponentReadiness(engine=False, dashboard=False, ingestion=False)
        paper = state.ComponentReadiness(engine=False, dashboard=True, ingestion=False)
        full = state.ComponentReadiness(engine=True, dashboard=True, ingestion=True)
        self.assertIn("install", state.next_step(none, False).lower())
        self.assertIn("paper mode is ready", state.next_step(paper, False).lower())
        self.assertIn("setup", state.next_step(full, False).lower())  # built, not configured
        self.assertIn("--live", state.next_step(full, True))  # built + configured


class ConfigTest(unittest.TestCase):
    def _layout_with_config(self, text: str) -> Layout:
        d = tempfile.mkdtemp()
        lo = Layout(Path(d))
        lo.ensure_state_dirs()
        lo.config_toml.write_text(text)
        return lo

    def test_placeholder_config_is_not_live_ready(self):
        lo = self._layout_with_config('ws_url = "wss://YOUR_ARBITRUM_WS"\n')
        self.assertFalse(config.config_is_live_ready(lo))

    def test_filled_config_is_live_ready(self):
        lo = self._layout_with_config('ws_url = "wss://arb1.example.com"\naddr = "0x1234"\n')
        self.assertTrue(config.config_is_live_ready(lo))

    def test_missing_config_is_not_live_ready(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(config.config_is_live_ready(Layout(Path(d))))

    def test_dashboard_env_live_selects_external_feed(self):
        lo = Layout(Path("/tmp/ws"))
        env = config.dashboard_env(lo, live=True, port=8787)
        self.assertEqual(env["DATA_SOURCE"], "external")
        self.assertEqual(env["INGEST_FEED_URL"], "ws://127.0.0.1:9001")
        self.assertEqual(env["EXECUTION_MODE"], "paper")  # execution stays gated

    def test_dashboard_env_paper_is_simulated(self):
        lo = Layout(Path("/tmp/ws"))
        env = config.dashboard_env(lo, live=False, port=9000)
        self.assertEqual(env["DATA_SOURCE"], "simulated")
        self.assertEqual(env["PORT"], "9000")

    def test_health_urls_target_the_served_ports(self):
        # Ingestion's /health is served on metrics_bind (:9100), not health_bind.
        self.assertEqual(config.health_url("engine", 8787), "http://127.0.0.1:8080/health")
        self.assertEqual(config.health_url("ingestion", 8787), "http://127.0.0.1:9100/health")
        self.assertEqual(config.health_url("dashboard", 9000), "http://127.0.0.1:9000/api/health")
        self.assertIsNone(config.health_url("contracts", 8787))


class PrereqParsingTest(unittest.TestCase):
    def test_version_regex(self):
        m = _VERSION_RE.search("cargo 1.94.1 (abc 2025)")
        self.assertEqual(tuple(int(g) for g in m.groups() if g is not None), (1, 94, 1))

    def test_version_compare(self):
        self.assertTrue(_ge((20, 1), (20,)))
        self.assertFalse(_ge((18, 0), (20,)))
        self.assertFalse(_ge(None, (1,)))


class MergePathTest(unittest.TestCase):
    def test_appends_new_dirs_preserving_order(self):
        got = merge_path("/a:/b", ["/c", "/d"], windows=False, sep=":")
        self.assertEqual(got, "/a:/b:/c:/d")

    def test_drops_duplicates_and_empties(self):
        got = merge_path("/a::/b", ["/b", "/c", "  "], windows=False, sep=":")
        self.assertEqual(got, "/a:/b:/c")

    def test_windows_dedup_is_case_and_trailing_slash_insensitive(self):
        got = merge_path(
            "C:\\Python312;C:\\Node",
            ["c:\\python312\\", "C:\\Cargo\\bin"],
            windows=True,
            sep=";",
        )
        # The already-present Python dir (different case/slash) is not re-added.
        self.assertEqual(got, "C:\\Python312;C:\\Node;C:\\Cargo\\bin")


class ResolveExecutableTest(unittest.TestCase):
    """`_resolve` is what makes a bare command name (e.g. "pnpm") launch correctly
    on Windows even when the real file on PATH is a `.cmd`/`.bat` shim — see the
    docstring on `l2arb.proc._resolve`. The PATHEXT-matching part of that is
    exercised by Python's own (platform-gated) `shutil.which`; what's tested here
    is `_resolve`'s own contract: substitute the resolved path when found, and
    never crash or drop arguments when it isn't.
    """

    def _tool_dir_with(self, name: str) -> str:
        d = tempfile.mkdtemp()
        tool = Path(d) / name
        tool.write_text("#!/bin/sh\n")
        tool.chmod(0o755)
        return d

    def test_substitutes_the_resolved_path(self):
        d = self._tool_dir_with("mytool")
        resolved = _resolve(["mytool", "install", "--flag"], {"PATH": d})
        self.assertEqual(resolved, [str(Path(d) / "mytool"), "install", "--flag"])

    def test_leaves_command_unchanged_when_not_found_on_path(self):
        d = self._tool_dir_with("mytool")
        cmd = ["definitely-not-a-real-tool-xyz", "install"]
        self.assertEqual(_resolve(cmd, {"PATH": d}), cmd)

    def test_empty_command_is_a_no_op(self):
        self.assertEqual(_resolve([], {"PATH": "/usr/bin"}), [])


class ConsoleUtf8Test(unittest.TestCase):
    """Guards against the Windows UnicodeEncodeError regression: a console/pipe
    pinned to a legacy codepage (e.g. cp1252) must not crash on the ✓/✗/▶/─
    symbols `console.py` prints — see `_force_utf8_streams`."""

    def test_force_utf8_streams_reconfigures_stdout_and_stderr_to_utf8(self):
        calls = []

        class FakeStream:
            def reconfigure(self, **kwargs):
                calls.append(kwargs)

        with mock.patch.object(console.sys, "stdout", FakeStream()), mock.patch.object(console.sys, "stderr", FakeStream()):
            console._force_utf8_streams()

        self.assertEqual(calls, [{"encoding": "utf-8", "errors": "replace"}] * 2)

    def test_force_utf8_streams_never_raises_when_reconfigure_fails(self):
        class BrokenStream:
            def reconfigure(self, **kwargs):
                raise ValueError("boom")

        with mock.patch.object(console.sys, "stdout", BrokenStream()), mock.patch.object(console.sys, "stderr", BrokenStream()):
            console._force_utf8_streams()  # must not raise

    def test_force_utf8_streams_skips_streams_without_reconfigure(self):
        class NoReconfigure:
            pass

        with mock.patch.object(console.sys, "stdout", NoReconfigure()), mock.patch.object(console.sys, "stderr", NoReconfigure()):
            console._force_utf8_streams()  # must not raise

    def test_a_cp1252_pinned_stdout_no_longer_crashes_on_symbols(self):
        """End-to-end regression check for the reported crash: printing the ✓/▶/─
        symbols through a stream hard-pinned to cp1252 (what Windows falls back to
        for piped output, e.g. PowerShell's `| Out-Host`) must not raise."""
        import io

        buf = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
        with mock.patch.object(console.sys, "stdout", buf):
            console._force_utf8_streams()
            console.step("build starting → staging payload")  # → = the reported crash char
            console.ok("done")
            buf.flush()


class ProcRunUtf8Test(unittest.TestCase):
    """Guards against the Windows UnicodeDecodeError regression: reading a build
    tool's (pnpm, tsup, …) UTF-8 stdout through `proc.run`'s pipe must not crash
    when the process locale's preferred encoding is a legacy codepage like
    cp1252 — see `proc.run`'s docstring."""

    def test_run_pins_utf8_and_replace_on_the_subprocess_pipe(self):
        captured = {}

        class FakePopen:
            def __init__(self, *args, **kwargs):
                captured.update(kwargs)
                self.stdout = iter(())

            def wait(self):
                return 0

        with mock.patch.object(proc.subprocess, "Popen", FakePopen):
            rc = proc.run(["true"])

        self.assertEqual(rc, 0)
        self.assertEqual(captured.get("encoding"), "utf-8")
        self.assertEqual(captured.get("errors"), "replace")

    def test_run_decodes_utf8_subprocess_output_invalid_under_cp1252(self):
        """"”" (a right double quotation mark, plausible in real CLI output)
        encodes to UTF-8 bytes e2 80 9d — 0x9d has no cp1252 mapping, so decoding
        it under cp1252 (what `text=True` falls back to without a pinned
        ``encoding`` on a Windows box) raises UnicodeDecodeError, exactly like the
        reported crash on byte 0x8f."""
        script = "import sys; sys.stdout.buffer.write('”\\n'.encode('utf-8'))"
        buf = io.StringIO()
        with mock.patch.object(proc.sys, "stdout", buf):
            rc = proc.run([sys.executable, "-c", script])

        self.assertEqual(rc, 0)
        self.assertIn("”", buf.getvalue())


class PrereqsRunUtf8Test(unittest.TestCase):
    """Same class of regression as `ProcRunUtf8Test`, for the toolchain-detection
    subprocess helper — also on the winget auto-install path, whose progress
    output is UTF-8."""

    def test_run_pins_utf8_and_replace(self):
        captured = {}

        class FakeCompleted:
            returncode = 0
            stdout = ""
            stderr = ""

        def fake_run(cmd, **kwargs):
            captured.update(kwargs)
            return FakeCompleted()

        with mock.patch.object(prereqs.subprocess, "run", fake_run):
            rc, _out = prereqs._run(["true"])

        self.assertEqual(rc, 0)
        self.assertEqual(captured.get("encoding"), "utf-8")
        self.assertEqual(captured.get("errors"), "replace")


class _FakeStdin:
    """Minimal stand-in for `sys.stdin` exposing only what `_should_pause_before_exit`
    reads, following the same fake-stream pattern as `ConsoleUtf8Test` above."""

    def __init__(self, is_tty: bool):
        self._is_tty = is_tty

    def isatty(self) -> bool:
        return self._is_tty


class CliEntrypointTest(unittest.TestCase):
    """Regression coverage for the "exe window vanishes on failure" bug: double-
    clicking a frozen .exe spawns a console that Windows closes the instant the
    process exits, so `_entrypoint` must keep it open on *any* failure — a
    handled non-zero return, or an uncaught exception — long enough to read why.
    Before the fix, `_should_pause_before_exit`'s stdin check was inverted (it
    skipped the pause exactly when double-clicked), and nothing wrapped `main()`,
    so a crash bypassed the pause entirely. See `l2arb.cli._run_main_safely` and
    `_should_pause_before_exit`.
    """

    def test_pauses_when_frozen_windows_failed_and_interactive(self):
        with mock.patch.object(cli, "is_frozen", return_value=True), mock.patch.object(cli, "IS_WINDOWS", True), mock.patch.object(cli.sys, "stdin", _FakeStdin(True)):
            self.assertTrue(cli._should_pause_before_exit(1))

    def test_no_pause_on_success(self):
        with mock.patch.object(cli, "is_frozen", return_value=True), mock.patch.object(cli, "IS_WINDOWS", True), mock.patch.object(cli.sys, "stdin", _FakeStdin(True)):
            self.assertFalse(cli._should_pause_before_exit(0))

    def test_no_pause_when_not_frozen(self):
        with mock.patch.object(cli, "is_frozen", return_value=False), mock.patch.object(cli, "IS_WINDOWS", True), mock.patch.object(cli.sys, "stdin", _FakeStdin(True)):
            self.assertFalse(cli._should_pause_before_exit(1))

    def test_no_pause_when_not_windows(self):
        with mock.patch.object(cli, "is_frozen", return_value=True), mock.patch.object(cli, "IS_WINDOWS", False), mock.patch.object(cli.sys, "stdin", _FakeStdin(True)):
            self.assertFalse(cli._should_pause_before_exit(1))

    def test_no_pause_when_stdin_is_not_interactive(self):
        # Redirected/piped/closed stdin: nobody is there to press Enter, and
        # input() would just hang a non-interactive run instead of helping.
        with mock.patch.object(cli, "is_frozen", return_value=True), mock.patch.object(cli, "IS_WINDOWS", True), mock.patch.object(cli.sys, "stdin", _FakeStdin(False)):
            self.assertFalse(cli._should_pause_before_exit(1))

    def test_run_main_safely_returns_mains_code_on_success(self):
        with mock.patch.object(cli, "main", return_value=0):
            self.assertEqual(cli._run_main_safely([]), 0)

    def test_run_main_safely_survives_an_uncaught_exception(self):
        """The historical bug: an exception `main()` doesn't itself catch (e.g. a
        crash while unpacking the first-run payload, which runs before `main`'s
        own try/except) used to propagate straight out of `_entrypoint`, skipping
        the pause entirely. `_run_main_safely` must convert it to an exit code."""

        def boom(argv):
            raise RuntimeError("first-run payload unpack failed")

        with mock.patch.object(cli, "main", side_effect=boom):
            self.assertEqual(cli._run_main_safely([]), 1)

    def test_run_main_safely_maps_keyboard_interrupt_to_130(self):
        def interrupted(argv):
            raise KeyboardInterrupt

        with mock.patch.object(cli, "main", side_effect=interrupted):
            self.assertEqual(cli._run_main_safely([]), 130)


if __name__ == "__main__":
    unittest.main()
