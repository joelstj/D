"""Regression tests for the UTF-8 config corruption (see l2arb/textio.py).

The failure these lock down was real and shipped: on Windows, Python's default
text encoding is the ANSI codepage, so ``config.toml``'s generated header — which
contains an em dash — was written with a lone 0x97 byte. The Rust ingestion
binary reads its config with ``std::fs::read_to_string`` (UTF-8 only) and died on
every launch with ``stream did not contain valid UTF-8``, leaving the health HUD
showing ingestion permanently ``failed`` while the other services stayed green.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# Make the launcher package importable when run from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from l2arb import cli, config, setup, textio  # noqa: E402
from l2arb.paths import Layout  # noqa: E402


class TextIoTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_write_text_is_utf8_for_non_ascii(self) -> None:
        path = self.root / "a.toml"
        textio.write_text(path, "# em dash — section §\n")
        self.assertEqual(path.read_bytes().decode("utf-8"), "# em dash — section §\n")

    def test_write_text_uses_lf_newlines_on_every_platform(self) -> None:
        path = self.root / "b.toml"
        textio.write_text(path, "a\nb\n")
        self.assertEqual(path.read_bytes(), b"a\nb\n")

    def test_read_text_strips_a_bom(self) -> None:
        # Notepad-style BOM: valid UTF-8, but not valid TOML — it would turn the
        # original failure into an equally opaque parse error.
        path = self.root / "c.toml"
        path.write_bytes(b"\xef\xbb\xbfschema_version = 1\n")
        self.assertEqual(textio.read_text(path), "schema_version = 1\n")

    def test_is_valid_utf8_rejects_legacy_codepage_bytes(self) -> None:
        path = self.root / "d.toml"
        path.write_bytes("# L2 Arbitrage Bot — x\n".encode("cp1252"))
        self.assertFalse(textio.is_valid_utf8(path))

    def test_is_valid_utf8_false_for_missing_file(self) -> None:
        self.assertFalse(textio.is_valid_utf8(self.root / "nope.toml"))

    def test_repair_encoding_recovers_the_original_text(self) -> None:
        path = self.root / "e.toml"
        original = "# L2 Arbitrage Bot — quick-start\nschema_version = 1\n"
        path.write_bytes(original.encode("cp1252"))

        self.assertTrue(textio.repair_encoding(path))
        self.assertTrue(textio.is_valid_utf8(path))
        self.assertEqual(textio.read_text(path), original)

    def test_repair_encoding_keeps_a_backup(self) -> None:
        path = self.root / "f.toml"
        raw = "# — \n".encode("cp1252")
        path.write_bytes(raw)
        textio.repair_encoding(path)
        self.assertEqual((self.root / "f.toml.bak").read_bytes(), raw)

    def test_repair_encoding_is_a_noop_for_valid_utf8(self) -> None:
        path = self.root / "g.toml"
        textio.write_text(path, "# — ok\n")
        before = path.read_bytes()
        self.assertFalse(textio.repair_encoding(path))
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse((self.root / "g.toml.bak").exists())

    def test_read_text_recovers_legacy_bytes_instead_of_raising(self) -> None:
        """The second half of the field failure: fixing the *writer* left the
        *reader* strict, so a config already corrupted by the old writer — the
        exact population `repair_encoding` exists to rescue — killed the launcher
        on the way to being healed, with `UnicodeDecodeError: invalid start byte`.
        """
        path = self.root / "h.toml"
        original = "# L2 Arbitrage Bot — Arbitrum quick-start\nschema_version = 1\n"
        path.write_bytes(original.encode("cp1252"))

        self.assertEqual(textio.read_text(path), original)

    def test_read_text_still_raises_for_a_missing_file(self) -> None:
        # Tolerating a *decodable* file must not blur into tolerating an absent
        # one — callers distinguish the two, and silently returning "" for a
        # missing config would make it look empty rather than absent.
        with self.assertRaises(OSError):
            textio.read_text(self.root / "does-not-exist.toml")

    def test_read_text_recovery_agrees_with_repair_encoding(self) -> None:
        # A read and a repair must never disagree about what the file says,
        # otherwise a value could be validated before the repair and a different
        # one used after it.
        path = self.root / "i.toml"
        original = "# — §  ±\nschema_version = 1\n"
        path.write_bytes(original.encode("cp1252"))

        via_read = textio.read_text(path)
        textio.repair_encoding(path)
        self.assertEqual(via_read, textio.read_text(path))


class GeneratedConfigEncodingTest(unittest.TestCase):
    """The generated configs are the files that actually broke in the field."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.lo = Layout(self.root)

    def _ship_pools(self) -> None:
        pools = self.lo.ingestion / "config" / "pools"
        pools.mkdir(parents=True, exist_ok=True)
        textio.write_text(pools / "arbitrum.example.toml", "# real arbitrum pools\n[[pool]]\nkind='v3'\n")

    def test_generated_configs_really_do_contain_non_ascii(self) -> None:
        # Guards the premise of every test below: if these ever become pure
        # ASCII the encoding hazard is masked, not fixed, and a regression in
        # textio would stop being caught here.
        quickstart = setup.arbitrum_quickstart_config("wss://x", "https://x", "/p.toml")
        self.assertFalse(quickstart.isascii())
        self.assertFalse(setup.multi_chain_config(["[[chains]]\n"]).isascii())

    def test_quickstart_config_is_written_as_valid_utf8(self) -> None:
        self._ship_pools()
        written = setup.write_arbitrum_quickstart(self.lo, "wss://a.example/k", "https://a.example/k")
        assert written is not None
        self.assertTrue(textio.is_valid_utf8(written))
        self.assertIn("— Arbitrum quick-start", textio.read_text(written))

    def test_all_chains_config_is_written_as_valid_utf8(self) -> None:
        self.lo.ensure_state_dirs()
        textio.write_text(self.lo.config_toml, setup.multi_chain_config(["[[chains]]\nname = \"base\"\n"]))
        self.assertTrue(textio.is_valid_utf8(self.lo.config_toml))

    def test_ensure_config_toml_repairs_a_config_broken_by_the_old_writer(self) -> None:
        """The exact field failure: an install created before the fix keeps a
        config the ingestion binary cannot read, so it must self-heal rather
        than needing the user to delete a file under AppData."""
        self.lo.ensure_state_dirs()
        good = setup.arbitrum_quickstart_config("wss://a", "https://a", "/p.toml")
        # Byte-for-byte what the pre-fix `write_text()` emitted on a cp1252 box.
        self.lo.config_toml.write_bytes(good.encode("cp1252"))
        self.assertFalse(textio.is_valid_utf8(self.lo.config_toml))

        config.ensure_config_toml(self.lo)

        self.assertTrue(textio.is_valid_utf8(self.lo.config_toml))
        self.assertEqual(textio.read_text(self.lo.config_toml), good)

    def test_ensure_config_toml_leaves_a_healthy_config_untouched(self) -> None:
        self.lo.ensure_state_dirs()
        textio.write_text(self.lo.config_toml, "# fine — really\nschema_version = 1\n")
        before = self.lo.config_toml.read_bytes()
        config.ensure_config_toml(self.lo)
        self.assertEqual(self.lo.config_toml.read_bytes(), before)

    def test_config_is_live_ready_survives_a_bom(self) -> None:
        self.lo.ensure_state_dirs()
        body = setup.arbitrum_quickstart_config("wss://a", "https://a", "/p.toml")
        self.lo.config_toml.write_bytes(b"\xef\xbb\xbf" + body.encode("utf-8"))
        self.assertTrue(config.config_is_live_ready(self.lo))

    def test_config_is_live_ready_survives_a_legacy_encoded_config(self) -> None:
        """The reported crash, reproduced end to end.

        `l2arb run` on an install predating the UTF-8 fix went straight into
        `_effective_live` -> `config_is_live_ready` -> `read_text`, which raised
        ``UnicodeDecodeError: 'utf-8' codec can't decode byte 0x97 in position
        19`` and took the whole launch down. Position 19 is the em dash in the
        generated header, encoded by cp1252 as the single byte 0x97.
        """
        self.lo.ensure_state_dirs()
        body = setup.arbitrum_quickstart_config("wss://a", "https://a", "/p.toml")
        self.lo.config_toml.write_bytes(body.encode("cp1252"))
        # Guard the premise: this really is the byte from the reported traceback.
        self.assertEqual(self.lo.config_toml.read_bytes()[19], 0x97)

        self.assertTrue(config.config_is_live_ready(self.lo))


class AutoLaunchRepairsConfigTest(unittest.TestCase):
    """`ensure_config_toml` carries the encoding self-heal, but `cmd_auto` only
    called it while *installing* — so an install that already existed, which is
    the only kind that can hold a config written by an older launcher, never
    reached the repair. The next thing to touch the config crashed instead.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.lo = Layout(self.root)
        self.lo.ensure_state_dirs()

    def test_auto_repairs_the_config_on_an_already_installed_workspace(self) -> None:
        good = setup.arbitrum_quickstart_config("wss://a", "https://a", "/p.toml")
        self.lo.config_toml.write_bytes(good.encode("cp1252"))

        args = argparse.Namespace(
            paper_only=False, no_setup=True, no_probe=True, no_browser=True,
            live=False, paper=True, port=None,
        )
        with mock.patch.object(cli.state, "probe", return_value=cli.state.ComponentReadiness(True, True, True)), \
                mock.patch.object(cli, "cmd_run", return_value=0) as run_cmd:
            self.assertEqual(cli.cmd_auto(self.lo, args), 0)

        run_cmd.assert_called_once()
        # Healed on disk, so the Rust ingestion binary — which has no fallback —
        # can read it too, and with its original text intact.
        self.assertTrue(textio.is_valid_utf8(self.lo.config_toml))
        self.assertEqual(textio.read_text(self.lo.config_toml), good)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
