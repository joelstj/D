"""Tests for the SQLite credential store."""

from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

# Make the launcher package importable when run from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from l2arb import credentials  # noqa: E402
from l2arb.paths import Layout  # noqa: E402


class MaskTest(unittest.TestCase):
    def test_mask_hides_the_middle(self) -> None:
        masked = credentials.mask("abcdef123456")
        self.assertTrue(masked.startswith("ab"))
        self.assertTrue(masked.endswith("56"))
        self.assertNotIn("cdef1234", masked)

    def test_mask_reveals_nothing_for_short_values(self) -> None:
        self.assertEqual(credentials.mask("abcd"), "••••")

    def test_mask_of_empty(self) -> None:
        self.assertEqual(credentials.mask(""), "(empty)")

    def test_mask_url_hides_the_whole_key_segment(self) -> None:
        url = "https://arb-mainnet.g.alchemy.com/v2/SECRETKEY123"
        masked = credentials.mask_url(url)
        self.assertIn("arb-mainnet.g.alchemy.com", masked)
        self.assertIn("/v2/", masked)
        self.assertNotIn("SECRETKEY123", masked)
        # Not even a prefix or suffix of the credential leaks.
        self.assertNotIn("SE", masked)
        self.assertNotIn("23", masked)

    def test_mask_url_keeps_a_bare_host(self) -> None:
        self.assertEqual(credentials.mask_url("https://mainnet.base.org"), "https://mainnet.base.org")

    def test_mask_url_falls_back_for_a_non_url(self) -> None:
        self.assertEqual(credentials.mask_url("plainsecret"), credentials.mask("plainsecret"))


class StoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.lo = Layout(Path(self._tmp.name))

    def test_database_is_created_on_first_use(self) -> None:
        path = credentials.db_path(self.lo)
        self.assertFalse(path.exists())
        with credentials.store(self.lo) as st:
            self.assertIsNotNone(st.created_at())
        self.assertTrue(path.exists())
        self.assertEqual(path.name, "credentials.db")

    def test_set_get_roundtrip(self) -> None:
        with credentials.store(self.lo) as st:
            st.set("rpc.base.http", "https://x/y", category="rpc", is_secret=True)
            self.assertEqual(st.get("rpc.base.http"), "https://x/y")

    def test_values_persist_across_reopen(self) -> None:
        with credentials.store(self.lo) as st:
            st.set("a.key", "value-1")
        with credentials.store(self.lo) as st:
            self.assertEqual(st.get("a.key"), "value-1")

    def test_set_overwrites(self) -> None:
        with credentials.store(self.lo) as st:
            st.set("a.key", "first")
            st.set("a.key", "second")
            self.assertEqual(st.get("a.key"), "second")
            self.assertEqual(len(st.entries()), 1)

    def test_get_missing_is_none(self) -> None:
        with credentials.store(self.lo) as st:
            self.assertIsNone(st.get("nope"))

    def test_delete(self) -> None:
        with credentials.store(self.lo) as st:
            st.set("a.key", "v")
            self.assertTrue(st.delete("a.key"))
            self.assertFalse(st.delete("a.key"))
            self.assertIsNone(st.get("a.key"))

    def test_entry_display_masks_only_secrets(self) -> None:
        with credentials.store(self.lo) as st:
            st.set("open", "visible-value", is_secret=False)
            st.set("shut", "https://host/v2/SECRET", is_secret=True)
            display = {e.key: e.display() for e in st.entries()}
        self.assertEqual(display["open"], "visible-value")
        self.assertNotIn("SECRET", display["shut"])

    def test_as_dict(self) -> None:
        with credentials.store(self.lo) as st:
            st.set("a", "1")
            st.set("b", "2")
            self.assertEqual(st.as_dict(), {"a": "1", "b": "2"})

    def test_health_history_records_and_returns_latest(self) -> None:
        with credentials.store(self.lo) as st:
            st.record_health(mode="check", percent=50.0, satisfied=1, total=2)
            st.record_health(mode="live", percent=100.0, satisfied=2, total=2)
            last = st.last_health()
        assert last is not None
        self.assertEqual(last["percent"], 100.0)
        self.assertEqual(last["mode"], "live")

    def test_last_health_is_none_before_any_run(self) -> None:
        with credentials.store(self.lo) as st:
            self.assertIsNone(st.last_health())

    @unittest.skipIf(os.name == "nt", "POSIX permission bits only")
    def test_database_is_owner_only(self) -> None:
        with credentials.store(self.lo):
            pass
        mode = credentials.db_path(self.lo).stat().st_mode
        self.assertEqual(stat.S_IMODE(mode) & 0o077, 0, "group/other must have no access")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
