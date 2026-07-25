"""Unit tests for the launcher's pure logic (no builds, no network).

Run with:  python -m unittest discover -s launcher/tests
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

# Make the launcher package importable when run from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from l2arb import config, state  # noqa: E402
from l2arb.paths import Layout, workspace_root  # noqa: E402
from l2arb.prereqs import _VERSION_RE, _ge, merge_path  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
