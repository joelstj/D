"""Tests for the requirements catalog and its validators."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

# Make the launcher package importable when run from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from l2arb import requirements  # noqa: E402
from l2arb.requirements import BLOCKING, OPTIONAL, RECOMMENDED  # noqa: E402


class ValidatorTest(unittest.TestCase):
    def test_https_url_accepts_a_real_endpoint(self) -> None:
        self.assertIsNone(requirements.validate_https_url("https://arb-mainnet.g.alchemy.com/v2/KEY"))

    def test_https_url_accepts_a_failover_list(self) -> None:
        self.assertIsNone(requirements.validate_https_url("https://primary/a, https://backup/b"))

    def test_https_url_rejects_a_websocket_url(self) -> None:
        error = requirements.validate_https_url("wss://arb-mainnet.g.alchemy.com/v2/KEY")
        self.assertIsNotNone(error)
        self.assertIn("https://", str(error))

    def test_https_url_rejects_empty_and_spaces(self) -> None:
        self.assertIsNotNone(requirements.validate_https_url(""))
        self.assertIsNotNone(requirements.validate_https_url("https://a b"))

    def test_ws_url_accepts_and_rejects(self) -> None:
        self.assertIsNone(requirements.validate_ws_url("wss://host/ws/v3/KEY"))
        self.assertIsNone(requirements.validate_ws_url("ws://127.0.0.1:8546"))
        self.assertIsNotNone(requirements.validate_ws_url("https://host/v2/KEY"))

    def test_address_validation(self) -> None:
        self.assertIsNone(requirements.validate_address("0x50A71dF7DfC5850e8434C7c8A564366F4980183b"))
        self.assertIsNotNone(requirements.validate_address("0x123"))
        self.assertIsNotNone(requirements.validate_address("50A71dF7DfC5850e8434C7c8A564366F4980183b"))
        # 40 characters, but 'z' is not hex.
        self.assertIsNotNone(requirements.validate_address("0x" + "z" * 40))

    def test_api_key_accepts_a_clean_key(self) -> None:
        self.assertIsNone(requirements.validate_api_key("9f2c1b7e4a8d"))

    def test_api_key_rejects_empty_and_internal_whitespace(self) -> None:
        self.assertIsNotNone(requirements.validate_api_key(""))
        self.assertIsNotNone(requirements.validate_api_key("   "))
        # Internal whitespace means the paste grabbed two fields, not one key.
        self.assertIsNotNone(requirements.validate_api_key("abc def"))

    def test_api_key_tolerates_a_trailing_newline_from_a_paste(self) -> None:
        # Copying from a provider dashboard routinely grabs a trailing newline;
        # stripping it is friendlier than rejecting an otherwise-correct key.
        self.assertIsNone(requirements.validate_api_key("9f2c1b7e4a8d\n"))

    def test_port_validation(self) -> None:
        self.assertIsNone(requirements.validate_port("8787"))
        self.assertIsNotNone(requirements.validate_port("not-a-number"))
        self.assertIsNotNone(requirements.validate_port("70000"))
        self.assertIsNotNone(requirements.validate_port("80"), "privileged ports need a warning")

    def test_tuning_ranges(self) -> None:
        self.assertIsNone(requirements.MAX_HOPS.validate("4"))
        self.assertIsNotNone(requirements.MAX_HOPS.validate("1"))
        self.assertIsNotNone(requirements.MAX_HOPS.validate("9"))
        self.assertIsNone(requirements.MIN_PROFIT_BPS.validate("5.5"))
        self.assertIsNotNone(requirements.MIN_PROFIT_BPS.validate("-1"))


class CatalogTest(unittest.TestCase):
    def test_one_chain_yields_its_two_endpoints(self) -> None:
        keys = [r.key for r in requirements.catalog(["arbitrum"])]
        self.assertIn("rpc.arbitrum.http", keys)
        self.assertIn("rpc.arbitrum.ws", keys)
        self.assertNotIn("rpc.base.http", keys)

    def test_chains_are_emitted_in_canonical_order(self) -> None:
        # Caller order must not leak into the report/wizard ordering.
        keys = [r.key for r in requirements.catalog(["ink", "arbitrum", "base"])]
        chain_keys = [k for k in keys if k.endswith(".http")]
        self.assertEqual(chain_keys, ["rpc.arbitrum.http", "rpc.base.http", "rpc.ink.http"])

    def test_unknown_chain_is_ignored_not_raised(self) -> None:
        keys = [r.key for r in requirements.catalog(["arbitrum", "solana"])]
        self.assertIn("rpc.arbitrum.http", keys)

    def test_global_requirements_are_always_present(self) -> None:
        keys = [r.key for r in requirements.catalog([])]
        self.assertIn("engine.blockscout_api_key", keys)
        self.assertIn("wallet.profit_receiver", keys)

    def test_only_endpoints_are_blocking(self) -> None:
        """The percentage must mean something: only genuinely required values
        count toward it, so an unset optional knob can never hold it below 100."""
        for req in requirements.catalog(["arbitrum", "base"]):
            if req.tier == BLOCKING:
                self.assertIn(req.category, (requirements.CAT_RPC, requirements.CAT_WS), req.key)

    def test_profit_receiver_is_not_blocking(self) -> None:
        self.assertEqual(requirements.PROFIT_RECEIVER.tier, RECOMMENDED)

    def test_tuning_knobs_all_have_defaults(self) -> None:
        for req in requirements.catalog([]):
            if req.tier == OPTIONAL:
                self.assertIsNotNone(req.default, f"{req.key} is optional but has no default")

    def test_ws_requirement_is_derived_from_its_http_sibling(self) -> None:
        ws = requirements.by_key(["base"])["rpc.base.ws"]
        self.assertEqual(ws.derived_from, "rpc.base.http")

    def test_by_key_covers_the_catalog(self) -> None:
        chains = ["arbitrum", "optimism"]
        self.assertEqual(len(requirements.by_key(chains)), len(requirements.catalog(chains)))


class GuidanceTest(unittest.TestCase):
    """The user-facing promise: every prompt explains itself thoroughly.

    These lock the guidance in place so a requirement can never be added with a
    stub explanation — which would silently degrade the wizard into the bare
    "enter a value" prompt it exists to replace.
    """

    def _all(self) -> list:
        return requirements.catalog(list(requirements.CHAIN_ORDER))

    def test_every_requirement_explains_itself(self) -> None:
        for req in self._all():
            with self.subTest(req.key):
                self.assertTrue(req.title.strip(), "needs a title")
                self.assertGreater(len(req.what), 40, "'what it is' is too thin to help anyone")
                self.assertGreater(len(req.why), 40, "'why it's needed' is too thin")
                self.assertTrue(req.where, "'where to get it' must not be empty")
                self.assertTrue(req.looks_like.strip(), "needs an example value")

    def test_endpoint_guidance_names_somewhere_to_actually_get_one(self) -> None:
        for req in self._all():
            if req.category not in (requirements.CAT_RPC, requirements.CAT_API_KEY):
                continue
            with self.subTest(req.key):
                self.assertIn("http", " ".join(req.where).lower(), "should link somewhere concrete")

    def test_every_example_value_passes_its_own_validator(self) -> None:
        """A worked example that would itself be rejected is worse than none."""
        for req in self._all():
            for line in req.looks_like.splitlines():
                candidate = line.split("(")[0].split("·")[0].strip()
                if not candidate or "…" in candidate:
                    continue
                with self.subTest(req.key, example=candidate):
                    self.assertIsNone(req.validate(candidate))

    def test_every_suggestion_passes_its_own_validator(self) -> None:
        for req in self._all():
            for value, label in req.suggestions:
                with self.subTest(req.key, suggestion=value):
                    self.assertIsNone(req.validate(value))
                    self.assertTrue(label.strip())

    def test_public_endpoints_match_the_chain_they_are_offered_for(self) -> None:
        """Each fallback is the chain's own official endpoint (sourced from
        contracts/hardhat.config.js), so the suggestion cannot point at the
        wrong network."""
        expected = {
            "arbitrum": "arbitrum.io",
            "base": "base.org",
            "optimism": "optimism.io",
            "unichain": "unichain.org",
            "ink": "inkonchain.com",
        }
        for key, info in requirements.CHAINS.items():
            self.assertIn(expected[key], info.public_http)

    def test_wallet_guidance_warns_against_pasting_a_private_key(self) -> None:
        """The one place a user could be socially engineered into disclosing a
        key is the wallet box; the copy must actively warn there."""
        text = " ".join(requirements.PROFIT_RECEIVER.where).lower()
        self.assertIn("never", text)
        self.assertIn("seed phrase", text)

    def test_no_requirement_asks_for_a_private_key(self) -> None:
        """The detection stack holds no keys (root CLAUDE.md §2) — nothing in
        the catalog may collect one."""
        for req in self._all():
            blob = f"{req.key} {req.title} {req.what}".lower()
            self.assertNotIn("private key", blob, req.key)
            self.assertNotIn("seed phrase", blob, req.key)
            self.assertNotIn("mnemonic", blob, req.key)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
