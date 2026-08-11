"""Tests for the health check: resolution, scoring, probing and pool data."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from l2arb import credentials, healthcheck, requirements, textio
from l2arb.paths import Layout

REPO = Path(__file__).resolve().parents[2]

ALCHEMY_HTTP = "https://arb-mainnet.g.alchemy.com/v2/KEY"
ALCHEMY_WS = "wss://arb-mainnet.g.alchemy.com/v2/KEY"


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.lo = Layout(Path(self._tmp.name))
        self.store = credentials.open_store(self.lo)
        self.addCleanup(self.store.close)
        self._ship_pools()

    def _ship_pools(self) -> None:
        """Copy the repo's real, on-chain-verified pool registries in, so the
        pool checks exercise genuine shipped data rather than a stub."""
        src = REPO / "ingestion" / "config" / "pools"
        dst = self.lo.ingestion / "config" / "pools"
        dst.mkdir(parents=True, exist_ok=True)
        for path in src.glob("*.example.toml"):
            shutil.copyfile(path, dst / path.name)

    def _run(self, **kw):
        kw.setdefault("probe", False)
        kw.setdefault("env", {})
        kw.setdefault("check_build_state", False)
        return healthcheck.run(self.lo, self.store, **kw)


class ResolutionTest(_Base):
    def test_missing_value_is_reported_missing(self) -> None:
        report = self._run()
        item = next(i for i in report.items if i.key == "rpc.arbitrum.http")
        self.assertEqual(item.status, healthcheck.MISSING)
        self.assertEqual(item.source, healthcheck.SRC_NONE)

    def test_stored_value_is_used(self) -> None:
        self.store.set("rpc.arbitrum.http", ALCHEMY_HTTP)
        item = next(i for i in self._run().items if i.key == "rpc.arbitrum.http")
        self.assertEqual(item.status, healthcheck.OK)
        self.assertEqual(item.source, healthcheck.SRC_DB)

    def test_environment_beats_the_database(self) -> None:
        """Matches the precedence the repo documents for all config: an operator
        exporting a variable for one run expects it to win."""
        self.store.set("rpc.arbitrum.http", ALCHEMY_HTTP)
        env = {"L2ARB__CHAINS__ARBITRUM__HTTP": "https://from-env/v2/K"}
        item = next(i for i in self._run(env=env).items if i.key == "rpc.arbitrum.http")
        self.assertEqual(item.value, "https://from-env/v2/K")
        self.assertEqual(item.source, healthcheck.SRC_ENV)

    def test_empty_environment_variable_does_not_shadow_a_stored_value(self) -> None:
        """`export FOO=` is a common shell accident; it must not blank a good
        stored credential and drag the score down for no visible reason."""
        self.store.set("rpc.arbitrum.http", ALCHEMY_HTTP)
        item = next(
            i
            for i in self._run(env={"L2ARB__CHAINS__ARBITRUM__HTTP": "   "}).items
            if i.key == "rpc.arbitrum.http"
        )
        self.assertEqual(item.value, ALCHEMY_HTTP)
        self.assertEqual(item.source, healthcheck.SRC_DB)

    def test_default_is_used_for_optional_tuning(self) -> None:
        item = next(i for i in self._run().items if i.key == "engine.max_hops")
        self.assertEqual(item.status, healthcheck.OK)
        self.assertEqual(item.source, healthcheck.SRC_DEFAULT)
        self.assertEqual(item.value, "4")

    def test_invalid_stored_value_is_reported_with_the_reason(self) -> None:
        self.store.set("rpc.arbitrum.http", "wss://wrong-scheme/v2/K")
        item = next(i for i in self._run().items if i.key == "rpc.arbitrum.http")
        self.assertEqual(item.status, healthcheck.INVALID)
        self.assertIn("https://", item.detail)

    def test_secret_values_are_masked_in_the_report(self) -> None:
        self.store.set("rpc.arbitrum.http", "https://host/v2/SUPERSECRET")
        item = next(i for i in self._run().items if i.key == "rpc.arbitrum.http")
        self.assertNotIn("SUPERSECRET", item.display_value())


class ScoringTest(_Base):
    def test_fresh_install_defaults_to_arbitrum_only(self) -> None:
        self.assertEqual(self._run().chains, ["arbitrum"])

    def test_one_endpoint_pair_reaches_one_hundred_percent(self) -> None:
        self.store.set("rpc.arbitrum.http", ALCHEMY_HTTP)
        self.store.set("rpc.arbitrum.ws", ALCHEMY_WS)
        report = self._run()
        self.assertTrue(report.is_complete)
        self.assertEqual(report.percent, 100.0)

    def test_recommended_and_optional_gaps_do_not_hold_back_the_score(self) -> None:
        """100% must mean 'everything required is present', not 'every field is
        filled' — otherwise the number is unreachable and stops meaning anything."""
        self.store.set("rpc.arbitrum.http", ALCHEMY_HTTP)
        self.store.set("rpc.arbitrum.ws", ALCHEMY_WS)
        report = self._run()
        self.assertIsNone(self.store.get("wallet.profit_receiver"))
        self.assertTrue(report.is_complete)
        self.assertTrue(any(not i.satisfied for i in report.items))

    def test_more_chains_lowers_the_score_until_they_are_configured(self) -> None:
        self.store.set("rpc.arbitrum.http", ALCHEMY_HTTP)
        self.store.set("rpc.arbitrum.ws", ALCHEMY_WS)
        self.assertTrue(self._run().is_complete)
        report = self._run(chains=["arbitrum", "base"])
        self.assertFalse(report.is_complete)
        self.assertLess(report.percent, 100.0)

    def test_percent_counts_pool_data_as_well_as_endpoints(self) -> None:
        report = self._run()
        # 2 endpoints + 1 pool registry for a single chain.
        self.assertEqual(report.total_blocking, 3)

    def test_unsatisfied_lists_blocking_items_first(self) -> None:
        pending = self._run().unsatisfied()
        tiers = [i.requirement.tier for i in pending]
        self.assertEqual(tiers, sorted(tiers, key=lambda t: {"blocking": 0, "recommended": 1, "optional": 2}[t]))

    def test_is_ready_requires_the_build_too(self) -> None:
        self.store.set("rpc.arbitrum.http", ALCHEMY_HTTP)
        self.store.set("rpc.arbitrum.ws", ALCHEMY_WS)
        report = self._run()
        self.assertTrue(report.is_complete)
        self.assertFalse(report.is_ready, "nothing is built in this temp workspace")


class SelectedChainsTest(_Base):
    def test_defaults_to_arbitrum(self) -> None:
        self.assertEqual(healthcheck.selected_chains(self.store), ["arbitrum"])

    def test_reads_the_stored_selection_in_canonical_order(self) -> None:
        self.store.set(requirements.SELECTED_CHAINS_KEY, "ink,arbitrum")
        self.assertEqual(healthcheck.selected_chains(self.store), ["arbitrum", "ink"])

    def test_unknown_names_are_dropped(self) -> None:
        self.store.set(requirements.SELECTED_CHAINS_KEY, "base,dogecoin")
        self.assertEqual(healthcheck.selected_chains(self.store), ["base"])

    def test_all_unknown_falls_back_to_the_default(self) -> None:
        self.store.set(requirements.SELECTED_CHAINS_KEY, "dogecoin")
        self.assertEqual(healthcheck.selected_chains(self.store), ["arbitrum"])


class PoolCheckTest(_Base):
    def test_shipped_registry_is_materialised_automatically(self) -> None:
        """Pool addresses are on-chain facts, never something to type — the
        check fetches the verified shipped copy instead of prompting."""
        target = self.lo.state_dir / "pools" / "arbitrum.toml"
        self.assertFalse(target.exists())
        results = healthcheck.check_pools(self.lo, ["arbitrum"])
        self.assertTrue(target.exists())
        self.assertEqual(results[0].status, healthcheck.OK)
        self.assertGreater(results[0].pool_count, 0)

    def test_all_five_shipped_chains_have_real_pool_data(self) -> None:
        for result in healthcheck.check_pools(self.lo, list(requirements.CHAIN_ORDER)):
            with self.subTest(result.chain):
                self.assertEqual(result.status, healthcheck.OK)
                self.assertGreater(result.pool_count, 0)

    def test_missing_registry_is_blocking_and_says_how_to_fix_it(self) -> None:
        shutil.rmtree(self.lo.ingestion / "config" / "pools")
        result = healthcheck.check_pools(self.lo, ["base"])[0]
        self.assertEqual(result.status, healthcheck.MISSING)
        self.assertIn("discover_pools.py", result.detail)

    def test_empty_registry_is_reported_invalid(self) -> None:
        target = self.lo.state_dir / "pools" / "base.toml"
        target.parent.mkdir(parents=True, exist_ok=True)
        textio.write_text(target, "# no entries here\n")
        result = healthcheck.check_pools(self.lo, ["base"], repair=False)[0]
        self.assertEqual(result.status, healthcheck.INVALID)


class ProbeTest(_Base):
    def test_probe_confirms_the_expected_chain_id(self) -> None:
        self.store.set("rpc.arbitrum.http", ALCHEMY_HTTP)
        with mock.patch.object(healthcheck, "probe_chain_id", return_value=(42161, "")):
            item = next(i for i in self._run(probe=True).items if i.key == "rpc.arbitrum.http")
        self.assertEqual(item.status, healthcheck.OK)
        self.assertIn("42161", item.detail)

    def test_probe_catches_an_endpoint_for_the_wrong_network(self) -> None:
        """The most common real paste mistake: a Base URL in the Arbitrum box.
        Format validation cannot see it; only asking the endpoint can."""
        self.store.set("rpc.arbitrum.http", ALCHEMY_HTTP)
        with mock.patch.object(healthcheck, "probe_chain_id", return_value=(8453, "")):
            item = next(i for i in self._run(probe=True).items if i.key == "rpc.arbitrum.http")
        self.assertEqual(item.status, healthcheck.WRONG_CHAIN)
        self.assertIn("Base", item.detail)
        self.assertFalse(item.satisfied)

    def test_probe_reports_a_dead_endpoint(self) -> None:
        self.store.set("rpc.arbitrum.http", ALCHEMY_HTTP)
        with mock.patch.object(healthcheck, "probe_chain_id", return_value=(None, "could not connect")):
            item = next(i for i in self._run(probe=True).items if i.key == "rpc.arbitrum.http")
        self.assertEqual(item.status, healthcheck.UNREACHABLE)

    def test_unreachable_endpoint_keeps_the_score_below_one_hundred(self) -> None:
        self.store.set("rpc.arbitrum.http", ALCHEMY_HTTP)
        self.store.set("rpc.arbitrum.ws", ALCHEMY_WS)
        with mock.patch.object(healthcheck, "probe_chain_id", return_value=(None, "dead")), mock.patch.object(
            healthcheck, "probe_ws_reachable", return_value=(True, "ok")
        ):
            self.assertFalse(self._run(probe=True).is_complete)

    def test_websocket_probe_failure_is_reported(self) -> None:
        self.store.set("rpc.arbitrum.ws", ALCHEMY_WS)
        with mock.patch.object(healthcheck, "probe_ws_reachable", return_value=(False, "no route")):
            item = next(i for i in self._run(probe=True).items if i.key == "rpc.arbitrum.ws")
        self.assertEqual(item.status, healthcheck.UNREACHABLE)

    def test_no_probe_means_no_network_calls_at_all(self) -> None:
        self.store.set("rpc.arbitrum.http", ALCHEMY_HTTP)
        with mock.patch.object(healthcheck, "probe_chain_id") as probe:
            self._run(probe=False)
        probe.assert_not_called()

    def test_probe_chain_id_never_raises_on_a_dead_host(self) -> None:
        # Port 1 on localhost: nothing listens, so this exercises the real
        # error path rather than a mock of it.
        found, detail = healthcheck.probe_chain_id("http://127.0.0.1:1", timeout=2.0)
        self.assertIsNone(found)
        self.assertTrue(detail)

    def test_ws_probe_never_raises_on_a_dead_host(self) -> None:
        reachable, detail = healthcheck.probe_ws_reachable("ws://127.0.0.1:1", timeout=2.0)
        self.assertFalse(reachable)
        self.assertTrue(detail)

    def test_ws_probe_rejects_a_url_with_no_host(self) -> None:
        reachable, _ = healthcheck.probe_ws_reachable("wss://", timeout=1.0)
        self.assertFalse(reachable)


class EnvOverridesTest(_Base):
    def test_stored_values_become_child_environment_variables(self) -> None:
        self.store.set("engine.blockscout_api_key", "abc123")
        env = healthcheck.env_overrides(self.store, ["arbitrum"])
        self.assertEqual(env["L2ARB__BLOCKSCOUT__API_KEY"], "abc123")

    def test_the_real_environment_still_wins(self) -> None:
        self.store.set("engine.blockscout_api_key", "from-db")
        with mock.patch.dict("os.environ", {"L2ARB__BLOCKSCOUT__API_KEY": "from-shell"}):
            env = healthcheck.env_overrides(self.store, ["arbitrum"])
        self.assertNotIn("L2ARB__BLOCKSCOUT__API_KEY", env)

    def test_unset_values_are_not_exported(self) -> None:
        env = healthcheck.env_overrides(self.store, ["arbitrum"])
        self.assertNotIn("L2ARB__CHAINS__ARBITRUM__HTTP", env)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
