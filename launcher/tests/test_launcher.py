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

from l2arb import cli, config, console, payload, prereqs, proc, setup, state  # noqa: E402
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


class PayloadTest(unittest.TestCase):
    """`ensure_payload` unpacks the frozen exe's bundled component sources on
    first run. Previously untested (only exercised end-to-end by an actual
    frozen build), so a regression here (e.g. the concurrent-double-launch
    race below) had no unit-level guard at all.
    """

    def _frozen_layout_with_bundle(self, components: tuple[str, ...] = ("engine",)):
        bundle = Path(tempfile.mkdtemp())
        src = bundle / "payload"
        for comp in components:
            d = src / comp
            d.mkdir(parents=True)
            (d / "marker.txt").write_text(comp)
        root = Path(tempfile.mkdtemp())
        lo = Layout(root)
        return lo, bundle

    def test_dev_checkout_is_a_no_op(self):
        # Not frozen (a plain `python -m l2arb ...` dev invocation): must not
        # touch the filesystem or raise, regardless of bundle_dir()'s value.
        lo = Layout(Path(tempfile.mkdtemp()))
        with mock.patch.object(payload, "is_frozen", return_value=False):
            payload.ensure_payload(lo)  # must not raise
        self.assertFalse((lo.root / "engine").exists())

    def test_unpacks_each_bundled_component_once(self):
        lo, bundle = self._frozen_layout_with_bundle(("engine", "dashboard"))
        with mock.patch.object(payload, "is_frozen", return_value=True), mock.patch.object(
            payload, "bundle_dir", return_value=bundle
        ):
            payload.ensure_payload(lo)
        self.assertEqual((lo.root / "engine" / "marker.txt").read_text(), "engine")
        self.assertEqual((lo.root / "dashboard" / "marker.txt").read_text(), "dashboard")

    def test_already_unpacked_component_is_left_alone(self):
        lo, bundle = self._frozen_layout_with_bundle(("engine",))
        (lo.root / "engine").mkdir(parents=True)
        (lo.root / "engine" / "marker.txt").write_text("already installed, do not touch")
        with mock.patch.object(payload, "is_frozen", return_value=True), mock.patch.object(
            payload, "bundle_dir", return_value=bundle
        ):
            payload.ensure_payload(lo)
        self.assertEqual((lo.root / "engine" / "marker.txt").read_text(), "already installed, do not touch")

    def test_concurrent_double_launch_race_does_not_raise(self):
        # Regression: double-clicking the .exe twice starts two processes that
        # can both pass the `not d.exists()` check before either finishes
        # copytree — the loser used to crash with an uncaught FileExistsError
        # (payload unpack runs before main()'s own try/except) instead of
        # quietly deferring to the winner.
        lo, bundle = self._frozen_layout_with_bundle(("engine",))

        def racing_copytree(_s, d):
            # Simulate the other instance having just won the race by actually
            # creating the destination, then fail exactly as the real
            # shutil.copytree would against a pre-existing directory.
            Path(d).mkdir(parents=True)
            raise FileExistsError(17, "File exists", str(d))

        with mock.patch.object(payload, "is_frozen", return_value=True), mock.patch.object(
            payload, "bundle_dir", return_value=bundle
        ), mock.patch.object(payload.shutil, "copytree", racing_copytree):
            payload.ensure_payload(lo)  # must not raise


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

    def test_non_hex_0x_token_is_not_live_ready(self):
        # Regression: config.example.toml's Unichain V4 addresses
        # (uniswap_v4_pool_manager/_state_view = "0xUNICHAIN_V4_...") don't start
        # with "YOUR_"/"0xWETH"/"0xUSDC", so the literal _PLACEHOLDER_MARKERS list
        # alone missed them: a user who filled every RPC endpoint and every
        # WETH/USDC placeholder but left Unichain's V4 infra addresses untouched
        # was told the config was live-ready, and `run --live` would have launched
        # ingestion against a fake address instead of falling back to paper mode.
        lo = self._layout_with_config(
            'ws_url = "wss://real-arb.example.com"\n'
            'weth = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"\n'
            'usdc = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"\n'
            'uniswap_v4_pool_manager = "0xUNICHAIN_V4_POOLMANAGER"\n'
            'uniswap_v4_state_view   = "0xUNICHAIN_V4_STATEVIEW"\n'
        )
        self.assertFalse(config.config_is_live_ready(lo))

    def test_pure_hex_0x_tokens_do_not_false_positive(self):
        # The general net must not flag genuine addresses/infra constants (which
        # are always pure hex) as placeholders, else a real filled config would
        # wrongly be refused live-ready.
        lo = self._layout_with_config(
            'weth = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"\n'
            'multicall3 = "0xcA11bde05977b3631167028862bE2a173976CA11"\n'
            'zero = "0x0"\n'
        )
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

    def _layout_with_ingestion_tree(
        self,
        config_chains=("arbitrum", "base", "optimism", "unichain", "ink"),
        example_chains=None,
    ) -> Layout:
        """A workspace with a real-shaped ingestion/config tree: the example
        config has a [[chains]] block (referencing pool_registry by the plain,
        unmaterialised relative path) for every chain in `config_chains`, but
        only `example_chains` (defaults to all of `config_chains`) actually has
        a shipped pool .example.toml — lets a test model an incomplete
        ingestion tree without also lying about which chains the config itself
        declares."""
        if example_chains is None:
            example_chains = config_chains
        root = Path(tempfile.mkdtemp())
        pools_dir = root / "ingestion" / "config" / "pools"
        pools_dir.mkdir(parents=True)
        chain_blocks = "\n".join(
            f'[[chains]]\nname = "{c}"\npool_registry = "config/pools/{c}.toml"\n' for c in config_chains
        )
        (root / "ingestion" / "config" / "config.example.toml").write_text(
            f"schema_version = 1\n\n{chain_blocks}"
        )
        for c in example_chains:
            (pools_dir / f"{c}.example.toml").write_text(f"# real {c} pools\n[[pool]]\nkind='v3'\n")
        return Layout(root)

    def test_ensure_config_toml_materialises_and_rewrites_every_chains_pools(self):
        lo = self._layout_with_ingestion_tree()
        config.ensure_config_toml(lo)
        text = lo.config_toml.read_text()
        for c in ("arbitrum", "base", "optimism", "unichain", "ink"):
            pool_file = lo.state_dir / "pools" / f"{c}.toml"
            self.assertTrue(pool_file.exists(), f"{c} pool registry was not materialised")
            # The config references the materialised absolute path, not the
            # original relative "config/pools/<chain>.toml" placeholder.
            self.assertIn(str(pool_file), text)
            self.assertNotIn(f'pool_registry = "config/pools/{c}.toml"', text)

    def test_ensure_config_toml_leaves_pool_registry_alone_for_a_chain_with_no_shipped_example(self):
        # The config declares all 5 chains, but only Arbitrum's example ships —
        # mirrors an incomplete ingestion tree rather than assuming every
        # chain always has one.
        lo = self._layout_with_ingestion_tree(example_chains=("arbitrum",))
        config.ensure_config_toml(lo)
        text = lo.config_toml.read_text()
        self.assertNotIn('pool_registry = "config/pools/arbitrum.toml"', text)
        # base/optimism/unichain/ink had nothing to materialise, so their
        # placeholder reference is left exactly as shipped, not corrupted.
        self.assertIn('pool_registry = "config/pools/base.toml"', text)

    def test_ensure_config_toml_is_idempotent(self):
        lo = self._layout_with_ingestion_tree()
        config.ensure_config_toml(lo)
        first = lo.config_toml.read_text()
        config.ensure_config_toml(lo)
        self.assertEqual(lo.config_toml.read_text(), first)

    @unittest.skipUnless(sys.version_info >= (3, 11), "tomllib needs 3.11+")
    def test_ensure_config_toml_escapes_a_windows_style_pool_path(self):
        # Regression, same shape as setup.py's quick-start fix (root CLAUDE.md
        # §9 item 4): a backslash-heavy absolute path interpolated raw into a
        # TOML basic string becomes invalid escape sequences and the whole
        # generated config fails to parse. ensure_config_toml must go through
        # the same Windows-safe `_toml_str` escaping, not a raw f-string.
        # materialize_pool_registries is mocked directly (rather than pointing
        # state_dir at a Windows path) so the test does real filesystem I/O
        # only through normal, this-OS-native paths.
        import tomllib

        lo = self._layout_with_ingestion_tree(config_chains=("arbitrum",))
        win_path = r"C:\Users\Alice\AppData\Local\L2ArbBot\.l2arb\pools\arbitrum.toml"
        with mock.patch.object(setup, "materialize_pool_registries", return_value={"arbitrum": Path(win_path)}):
            config.ensure_config_toml(lo)
        parsed = tomllib.loads(lo.config_toml.read_text())
        self.assertEqual(parsed["chains"][0]["pool_registry"], win_path)


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

        class FakeStdout:
            """Mimics the bits of a real Popen(stdout=PIPE)'s TextIOWrapper that
            proc.run() actually uses: iterable, and closeable exactly once."""

            def __init__(self):
                self.closed = False

            def __iter__(self):
                return iter(())

            def close(self):
                self.closed = True

        class FakePopen:
            def __init__(self, *args, **kwargs):
                captured.update(kwargs)
                self.stdout = FakeStdout()

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
