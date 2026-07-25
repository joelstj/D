"""Unit tests for the guided-setup pure logic (no I/O, no network).

Run with:  python -m unittest discover -s launcher/tests
"""

from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from l2arb import config, setup  # noqa: E402
from l2arb.config import _PLACEHOLDER_MARKERS  # noqa: E402
from l2arb.paths import Layout  # noqa: E402


def _args(**kw):
    base = dict(provider=None, key=None, http=None, ws=None, backup=None)
    base.update(kw)
    return types.SimpleNamespace(**base)


def _never(_msg: str) -> str:  # a prompt that must not be called
    raise AssertionError("prompt should not be called when flags are supplied")


class ProviderPresetTest(unittest.TestCase):
    def test_alchemy_and_infura_arbitrum_urls(self):
        self.assertEqual(
            setup.provider_http_url("alchemy", "KEY123", "arbitrum"),
            "https://arb-mainnet.g.alchemy.com/v2/KEY123",
        )
        self.assertEqual(
            setup.provider_http_url("Infura", "  KEY123 ", "arbitrum"),
            "https://arbitrum-mainnet.infura.io/v3/KEY123",
        )

    def test_unknown_pairing_or_empty_key_returns_none(self):
        self.assertIsNone(setup.provider_http_url("alchemy", "KEY", "ink"))
        self.assertIsNone(setup.provider_http_url("pigeon", "KEY", "arbitrum"))
        self.assertIsNone(setup.provider_http_url("alchemy", "   ", "arbitrum"))


class DeriveWsTest(unittest.TestCase):
    def test_alchemy_shares_path(self):
        self.assertEqual(
            setup.derive_ws_url("https://arb-mainnet.g.alchemy.com/v2/K"),
            "wss://arb-mainnet.g.alchemy.com/v2/K",
        )

    def test_infura_moves_under_ws(self):
        self.assertEqual(
            setup.derive_ws_url("https://arbitrum-mainnet.infura.io/v3/K"),
            "wss://arbitrum-mainnet.infura.io/ws/v3/K",
        )

    def test_generic_https_swaps_scheme(self):
        self.assertEqual(setup.derive_ws_url("https://rpc.example.com/x"), "wss://rpc.example.com/x")

    def test_uses_only_the_primary_of_a_failover_list(self):
        got = setup.derive_ws_url("https://arb-mainnet.g.alchemy.com/v2/K , https://backup/x")
        self.assertEqual(got, "wss://arb-mainnet.g.alchemy.com/v2/K")


class ArbitrumQuickstartTest(unittest.TestCase):
    def _cfg(self) -> str:
        return setup.arbitrum_quickstart_config(
            ws_url="wss://arb-mainnet.g.alchemy.com/v2/K",
            http_url="https://arb-mainnet.g.alchemy.com/v2/K, https://backup/x",
            pool_registry="/home/user/.l2arb/pools/arbitrum.toml",
        )

    def test_contains_real_addresses_and_user_endpoints(self):
        cfg = self._cfg()
        self.assertIn(setup.ARBITRUM_WETH, cfg)
        self.assertIn(setup.ARBITRUM_USDC, cfg)
        self.assertIn(setup.ARBITRUM_WETH_USDC_POOL, cfg)
        self.assertIn("wss://arb-mainnet.g.alchemy.com/v2/K", cfg)
        self.assertIn("https://backup/x", cfg)  # failover backup preserved
        self.assertIn('pool_registry = "/home/user/.l2arb/pools/arbitrum.toml"', cfg)

    def test_has_no_placeholder_markers_so_it_reads_as_live_ready(self):
        cfg = self._cfg()
        for marker in _PLACEHOLDER_MARKERS:
            self.assertNotIn(marker, cfg, f"generated config must not contain placeholder {marker!r}")

    def test_cross_chain_disabled_and_single_chain(self):
        cfg = self._cfg()
        self.assertIn("[cross_chain]\nenabled = false", cfg)
        self.assertEqual(cfg.count("[[chains]]"), 1)

    @unittest.skipUnless(sys.version_info >= (3, 11), "tomllib needs 3.11+")
    def test_generated_config_is_valid_toml(self):
        import tomllib

        parsed = tomllib.loads(self._cfg())
        self.assertEqual(parsed["chains"][0]["chain_id"], 42161)
        self.assertEqual(parsed["chains"][0]["weth"], setup.ARBITRUM_WETH)
        self.assertFalse(parsed["cross_chain"]["enabled"])


class FillEndpointsTest(unittest.TestCase):
    def test_replaces_named_chain_endpoints_only(self):
        example = (
            'ws_url        = "wss://YOUR_ARBITRUM_WS"\n'
            'http_url      = "https://YOUR_ARBITRUM_ARCHIVE, https://YOUR_ARBITRUM_ARCHIVE_BACKUP"\n'
            'ws_url        = "wss://YOUR_BASE_WS"\n'
        )
        out = setup.fill_chain_endpoints(example, {"arbitrum": ("wss://real-arb", "https://real-arb-http")})
        self.assertIn('ws_url        = "wss://real-arb"', out)
        self.assertIn('http_url      = "https://real-arb-http"', out)
        self.assertIn('wss://YOUR_BASE_WS', out)  # base untouched


class ResolveEndpointsTest(unittest.TestCase):
    def test_http_flag_derives_ws(self):
        ws, http = setup.resolve_arbitrum_endpoints(
            _args(http="https://arb-mainnet.g.alchemy.com/v2/K"), _never
        )
        self.assertEqual(http, "https://arb-mainnet.g.alchemy.com/v2/K")
        self.assertEqual(ws, "wss://arb-mainnet.g.alchemy.com/v2/K")

    def test_provider_key_flags(self):
        ws, http = setup.resolve_arbitrum_endpoints(_args(provider="alchemy", key="K"), _never)
        self.assertEqual(http, "https://arb-mainnet.g.alchemy.com/v2/K")
        self.assertEqual(ws, "wss://arb-mainnet.g.alchemy.com/v2/K")

    def test_backup_flag_is_appended_for_failover(self):
        _ws, http = setup.resolve_arbitrum_endpoints(
            _args(http="https://primary/x", backup="https://backup/y"), _never
        )
        self.assertEqual(http, "https://primary/x, https://backup/y")

    def test_interactive_prompts_for_url_and_backup(self):
        answers = iter(["https://rpc.example.com/x", ""])  # url, then skip backup
        got = setup.resolve_arbitrum_endpoints(_args(), lambda _m: next(answers))
        self.assertEqual(got, ("wss://rpc.example.com/x", "https://rpc.example.com/x"))

    def test_empty_input_returns_none(self):
        self.assertIsNone(setup.resolve_arbitrum_endpoints(_args(), lambda _m: ""))


class WriteQuickstartTest(unittest.TestCase):
    def _layout_with_shipped_pools(self) -> Layout:
        root = Path(tempfile.mkdtemp())
        pools = root / "ingestion" / "config" / "pools"
        pools.mkdir(parents=True)
        (pools / "arbitrum.example.toml").write_text("# real arbitrum pools\n[[pool]]\nkind='v3'\n")
        return Layout(root)

    def test_writes_live_ready_config_and_materialises_pools(self):
        lo = self._layout_with_shipped_pools()
        cfg = setup.write_arbitrum_quickstart(lo, "wss://arb/x", "https://arb/x")
        self.assertIsNotNone(cfg)
        self.assertTrue(lo.config_toml.exists())
        # The generated config is recognised as live-ready (no placeholder markers).
        self.assertTrue(config.config_is_live_ready(lo))
        # The real pool registry was copied into the state dir and referenced.
        pool_file = lo.state_dir / "pools" / "arbitrum.toml"
        self.assertTrue(pool_file.exists())
        self.assertIn(str(pool_file), lo.config_toml.read_text())

    def test_missing_shipped_pools_is_a_clean_failure(self):
        lo = Layout(Path(tempfile.mkdtemp()))  # no ingestion/ tree
        self.assertIsNone(setup.write_arbitrum_quickstart(lo, "wss://x", "https://x"))


class ValidateConfigTest(unittest.TestCase):
    def test_missing_binary_is_not_a_failure(self):
        lo = Layout(Path(tempfile.mkdtemp()))
        ok, note = setup.validate_config(lo, runner=_never)  # runner unused when no binary
        self.assertTrue(ok)
        self.assertIn("not built", note)


if __name__ == "__main__":
    unittest.main()
