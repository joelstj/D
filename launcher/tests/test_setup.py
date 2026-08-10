"""Unit tests for the guided-setup pure logic (no I/O, no network).

Run with:  python -m unittest discover -s launcher/tests
"""

from __future__ import annotations

import json
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

    @unittest.skipUnless(sys.version_info >= (3, 11), "tomllib needs 3.11+")
    def test_windows_pool_path_produces_parseable_toml(self):
        # Regression: on Windows the materialised pool path is an absolute path
        # with backslashes (C:\Users\...). Interpolated raw into a TOML *basic*
        # string it became invalid escape sequences (\U, \A, \L, ...) and the
        # whole config failed to parse, silently breaking the live path on the
        # flagship .exe. The path must survive as-is.
        import tomllib

        win_path = r"C:\Users\Alice\AppData\Local\L2ArbBot\.l2arb\pools\arbitrum.toml"
        cfg = setup.arbitrum_quickstart_config(
            ws_url="wss://arb/ws", http_url="https://arb/http", pool_registry=win_path
        )
        parsed = tomllib.loads(cfg)  # must not raise TOMLDecodeError
        self.assertEqual(parsed["chains"][0]["pool_registry"], win_path)

    @unittest.skipUnless(sys.version_info >= (3, 11), "tomllib needs 3.11+")
    def test_pasted_rpc_url_with_a_stray_quote_produces_parseable_toml(self):
        # Regression: ws_url/http_url are pasted by the operator (from a provider
        # dashboard, a script, shell history, ...) and were interpolated raw into
        # the TOML basic string, same as the pool path used to be. A stray `"` or
        # `\` in the paste — e.g. surrounding quotes grabbed by accident — broke
        # out of the string and made the whole generated config.toml unparseable,
        # exactly the "silently breaking the live path" failure mode the pool-path
        # fix above already covers for a different field.
        import tomllib

        http_with_quote = 'https://arb-mainnet.g.alchemy.com/v2/abc"; evil = true #'
        ws_with_backslash = r"wss://arb\evil"
        cfg = setup.arbitrum_quickstart_config(
            ws_url=ws_with_backslash, http_url=http_with_quote, pool_registry="/tmp/pools/arbitrum.toml"
        )
        parsed = tomllib.loads(cfg)  # must not raise TOMLDecodeError
        self.assertEqual(parsed["chains"][0]["ws_url"], ws_with_backslash)
        self.assertEqual(parsed["chains"][0]["http_url"], http_with_quote)

    def test_toml_str_escapes_backslashes_and_quotes(self):
        self.assertEqual(setup._toml_str(r"C:\a\b"), '"C:\\\\a\\\\b"')
        self.assertEqual(setup._toml_str('a"b'), '"a\\"b"')
        self.assertEqual(setup._toml_str("/posix/path"), '"/posix/path"')


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


# ── Multi-chain guided setup (`l2arb setup --all-chains`) ────────────────────


class DetectEnvEndpointsTest(unittest.TestCase):
    def test_finds_rpc_url_chain_pattern(self):
        env = {"RPC_URL_BASE": "https://a"}
        self.assertEqual(setup.detect_env_endpoints("base", env), ("https://a", None))

    def test_finds_chain_rpc_url_pattern(self):
        env = {"OPTIMISM_RPC_URL": "https://b"}
        self.assertEqual(setup.detect_env_endpoints("optimism", env), ("https://b", None))

    def test_finds_l2arb_prefixed_http_and_wss(self):
        env = {"L2ARB__CHAINS__ARBITRUM__HTTP": "https://c", "L2ARB__CHAINS__ARBITRUM__WSS": "wss://c"}
        self.assertEqual(setup.detect_env_endpoints("arbitrum", env), ("https://c", "wss://c"))

    def test_absent_returns_none_each(self):
        self.assertEqual(setup.detect_env_endpoints("ink", {}), (None, None))

    def test_blank_value_is_treated_as_absent(self):
        self.assertEqual(setup.detect_env_endpoints("base", {"RPC_URL_BASE": "   "}), (None, None))

    def test_earlier_pattern_wins_over_later(self):
        env = {"RPC_URL_BASE": "https://first", "BASE_RPC_URL": "https://second"}
        http, _ws = setup.detect_env_endpoints("base", env)
        self.assertEqual(http, "https://first")


class ResolveChainEndpointsTest(unittest.TestCase):
    def test_env_endpoint_used_without_prompting(self):
        got = setup.resolve_chain_endpoints("base", {"RPC_URL_BASE": "https://base.example/x"}, _never)
        self.assertEqual(got, ("wss://base.example/x", "https://base.example/x"))

    def test_env_ws_preferred_over_derived(self):
        env = {"RPC_URL_ARBITRUM": "https://a", "L2ARB__CHAINS__ARBITRUM__WSS": "wss://real-ws"}
        got = setup.resolve_chain_endpoints("arbitrum", env, _never)
        self.assertEqual(got, ("wss://real-ws", "https://a"))

    def test_prompts_when_nothing_in_env(self):
        got = setup.resolve_chain_endpoints("ink", {}, lambda _m: "https://ink.example/x")
        self.assertEqual(got, ("wss://ink.example/x", "https://ink.example/x"))

    def test_empty_prompt_answer_skips_the_chain(self):
        self.assertIsNone(setup.resolve_chain_endpoints("ink", {}, lambda _m: ""))


class DiscoverPoolsJsonTest(unittest.TestCase):
    def _lo_with_script(self) -> Layout:
        root = Path(tempfile.mkdtemp())
        (root / "ingestion" / "scripts").mkdir(parents=True)
        (root / "ingestion" / "scripts" / "discover_pools.py").write_text("# stub\n")
        return Layout(root)

    def test_parses_the_runner_stdout_as_json(self):
        lo = self._lo_with_script()
        payload = {"chain": "base", "pools": [], "toml": None}
        result = setup._discover_pools_json(lo, "base", "https://x", lambda cmd, cwd=None: (1, json.dumps(payload)))
        self.assertEqual(result, payload)

    def test_missing_script_returns_none_without_calling_runner(self):
        lo = Layout(Path(tempfile.mkdtemp()))  # no ingestion/ tree at all
        result = setup._discover_pools_json(lo, "base", "https://x", _never)
        self.assertIsNone(result)

    def test_non_json_output_returns_none(self):
        lo = self._lo_with_script()
        result = setup._discover_pools_json(lo, "base", "https://x", lambda cmd, cwd=None: (1, "not json"))
        self.assertIsNone(result)


class PickNativePricePoolTest(unittest.TestCase):
    WETH = "0x4200000000000000000000000000000000000006"
    USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"

    def test_prefers_the_500_fee_tier(self):
        result = {
            "pools": [
                {"token0": self.WETH, "token1": self.USDC, "fee_pips": 3000, "address": "0xA"},
                {"token0": self.WETH, "token1": self.USDC, "fee_pips": 500, "address": "0xB"},
            ]
        }
        self.assertEqual(setup._pick_native_price_pool(result, self.WETH, self.USDC), "0xB")

    def test_falls_back_to_any_match_when_500_absent(self):
        result = {"pools": [{"token0": self.WETH, "token1": self.USDC, "fee_pips": 3000, "address": "0xA"}]}
        self.assertEqual(setup._pick_native_price_pool(result, self.WETH, self.USDC), "0xA")

    def test_ignores_pools_for_a_different_pair(self):
        other = "0x0000000000000000000000000000000000dEaD"
        result = {"pools": [{"token0": self.WETH, "token1": other, "fee_pips": 500, "address": "0xA"}]}
        self.assertIsNone(setup._pick_native_price_pool(result, self.WETH, self.USDC))

    def test_none_result_or_no_pools_returns_none(self):
        self.assertIsNone(setup._pick_native_price_pool(None, self.WETH, self.USDC))
        self.assertIsNone(setup._pick_native_price_pool({"pools": []}, self.WETH, self.USDC))


class MaterializeChainPoolsTest(unittest.TestCase):
    def _lo(self) -> Layout:
        root = Path(tempfile.mkdtemp())
        (root / "ingestion" / "scripts").mkdir(parents=True)
        (root / "ingestion" / "scripts" / "discover_pools.py").write_text("# stub\n")
        (root / "ingestion" / "config" / "pools").mkdir(parents=True)
        return Layout(root)

    def test_uses_discovered_pools_when_available(self):
        lo = self._lo()
        payload = {"pools": [{"address": "0xA"}], "toml": "[[pool]]\naddress='0xA'\n"}
        runner = lambda cmd, cwd=None: (0, json.dumps(payload))  # noqa: E731
        path, note, discovery = setup.materialize_chain_pools(lo, "base", "https://x", runner)
        self.assertEqual(path, lo.state_dir / "pools" / "base.toml")
        self.assertIn("discovered 1", note)
        self.assertEqual(path.read_text(), payload["toml"])
        self.assertEqual(discovery, payload)

    def test_falls_back_to_shipped_example_when_discovery_finds_nothing(self):
        lo = self._lo()
        (lo.ingestion / "config" / "pools" / "base.example.toml").write_text("# shipped real pools\n")
        runner = lambda cmd, cwd=None: (1, json.dumps({"pools": [], "toml": None}))  # noqa: E731
        path, note, _discovery = setup.materialize_chain_pools(lo, "base", "https://x", runner)
        self.assertEqual(path, lo.state_dir / "pools" / "base.toml")
        self.assertIn("shipped example", note)
        self.assertEqual(path.read_text(), "# shipped real pools\n")

    def test_neither_available_returns_none(self):
        lo = self._lo()  # no base.example.toml shipped
        runner = lambda cmd, cwd=None: (1, json.dumps({"pools": [], "toml": None}))  # noqa: E731
        path, note, _discovery = setup.materialize_chain_pools(lo, "unichain", "https://x", runner)
        self.assertIsNone(path)
        self.assertIn("no pool registry", note)


class RenderChainBlockTest(unittest.TestCase):
    def test_enabled_block_has_real_fields_and_native_price_pool(self):
        block = setup.render_chain_block("base", "wss://w", "https://h", "/pools/base.toml", "0xNATIVEPOOL")
        self.assertIn('name          = "base"', block)
        self.assertIn("enabled       = true", block)
        self.assertIn('ws_url        = "wss://w"', block)
        self.assertIn('"0xNATIVEPOOL"', block)

    def test_omits_native_price_pools_section_when_none_found(self):
        block = setup.render_chain_block("optimism", "wss://w", "https://h", "/pools/optimism.toml", None)
        self.assertNotIn("native_price_pools", block)

    @unittest.skipUnless(sys.version_info >= (3, 11), "tomllib needs 3.11+")
    def test_produces_valid_toml_as_part_of_a_full_config(self):
        import tomllib

        block = setup.render_chain_block("base", "wss://w", "https://h", "/pools/base.toml", "0x" + "1" * 40)
        parsed = tomllib.loads(block + "\n[cross_chain]\nenabled = false\n")
        self.assertTrue(parsed["chains"][0]["enabled"])
        self.assertEqual(parsed["chains"][0]["chain_id"], 8453)


class RenderDisabledChainBlockTest(unittest.TestCase):
    def test_disabled_block_preserves_the_endpoint(self):
        block = setup.render_disabled_chain_block("unichain", "wss://saved-ws", "https://saved-http", "/pools/unichain.toml")
        self.assertIn("enabled       = false", block)
        self.assertIn('ws_url        = "wss://saved-ws"', block)
        self.assertIn('http_url      = "https://saved-http"', block)
        self.assertIn("discover_pools.py", block)  # points at the concrete next step

    @unittest.skipUnless(sys.version_info >= (3, 11), "tomllib needs 3.11+")
    def test_produces_valid_toml(self):
        import tomllib

        block = setup.render_disabled_chain_block("ink", "wss://w", "https://h", "/pools/ink.toml")
        parsed = tomllib.loads(block + "\n[cross_chain]\nenabled = false\n")
        self.assertFalse(parsed["chains"][0]["enabled"])
        self.assertEqual(parsed["chains"][0]["hubs"], [])


class MultiChainConfigTest(unittest.TestCase):
    @unittest.skipUnless(sys.version_info >= (3, 11), "tomllib needs 3.11+")
    def test_assembles_a_valid_multi_chain_config(self):
        import tomllib

        blocks = [
            setup.render_chain_block("base", "wss://b", "https://b", "/pools/base.toml", None),
            setup.render_disabled_chain_block("ink", "wss://i", "https://i", "/pools/ink.toml"),
        ]
        text = setup.multi_chain_config(blocks)
        parsed = tomllib.loads(text)
        self.assertEqual(parsed["schema_version"], 1)
        self.assertFalse(parsed["cross_chain"]["enabled"])
        names = [c["name"] for c in parsed["chains"]]
        self.assertEqual(names, ["base", "ink"])


class RunSetupAllChainsTest(unittest.TestCase):
    def _lo_with_shipped_arbitrum_pools(self) -> Layout:
        root = Path(tempfile.mkdtemp())
        (root / "ingestion" / "scripts").mkdir(parents=True)
        (root / "ingestion" / "scripts" / "discover_pools.py").write_text("# stub\n")
        pools = root / "ingestion" / "config" / "pools"
        pools.mkdir(parents=True)
        (pools / "arbitrum.example.toml").write_text(
            '[[pool]]\ndex="uniswap_v3"\nkind="v3"\naddress="0x' + "1" * 40 + '"\n'
            'fee_pips=500\ntoken0="0x' + "2" * 40 + '"\ntoken1="0x' + "3" * 40 + '"\n'
        )
        return Layout(root)

    def _fake_runner_finds_pools_only_for(self, chains_with_pools: set[str]):
        weth, usdc = "0x" + "4" * 40, "0x" + "5" * 40

        def runner(cmd, cwd=None):
            chain = cmd[cmd.index("--chain") + 1]
            if chain in chains_with_pools:
                payload = {
                    "chain": chain,
                    "pools": [{"dex": "uniswap_v3", "address": "0x" + "6" * 40, "fee_pips": 500, "token0": weth, "token1": usdc}],
                    "toml": "[[pool]]\n# discovered\n",
                }
                return 0, json.dumps(payload)
            return 1, json.dumps({"chain": chain, "pools": [], "toml": None, "error": "no candidate"})

        return runner

    def test_env_detected_and_prompted_chains_both_go_live(self):
        lo = self._lo_with_shipped_arbitrum_pools()
        env = {"ARBITRUM_RPC_URL": "https://arb.example/x", "BASE_RPC_URL": "https://base.example/x"}
        answers = iter(["", "", ""])  # optimism, unichain, ink: all skipped
        rc = setup.run_setup_all_chains(
            lo, prompt=lambda _m: next(answers), env=env, runner=self._fake_runner_finds_pools_only_for({"base"})
        )
        self.assertEqual(rc, 0)
        self.assertTrue(lo.config_toml.exists())
        text = lo.config_toml.read_text()
        self.assertIn('name          = "arbitrum"', text)
        self.assertIn('name          = "base"', text)
        self.assertNotIn('name          = "optimism"', text)  # skipped chains are omitted entirely
        self.assertTrue(config.config_is_live_ready(lo))

    def test_chain_with_endpoint_but_no_pools_is_written_disabled_not_dropped(self):
        lo = self._lo_with_shipped_arbitrum_pools()
        env = {}
        # Prompt order matches KNOWN_CHAINS: arbitrum, base, optimism, unichain, ink.
        answers = iter(["https://arb.example/x", "", "", "https://unichain.example/x", ""])
        rc = setup.run_setup_all_chains(
            lo, prompt=lambda _m: next(answers), env=env, runner=self._fake_runner_finds_pools_only_for(set())
        )
        self.assertEqual(rc, 0)
        text = lo.config_toml.read_text()
        self.assertIn('name          = "unichain"', text)
        self.assertIn("enabled       = false", text)
        self.assertIn('ws_url        = "wss://unichain.example/x"', text)

    def test_no_chain_resolved_is_a_clean_non_fatal_failure(self):
        lo = self._lo_with_shipped_arbitrum_pools()
        rc = setup.run_setup_all_chains(lo, prompt=lambda _m: "", env={}, runner=self._fake_runner_finds_pools_only_for(set()))
        self.assertEqual(rc, 1)
        self.assertFalse(lo.config_toml.exists())


if __name__ == "__main__":
    unittest.main()
