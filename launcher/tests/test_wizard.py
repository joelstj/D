"""Tests for the guided setup wizard."""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# Make the launcher package importable when run from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from l2arb import credentials, healthcheck, requirements, textio, wizard  # noqa: E402
from l2arb.paths import Layout  # noqa: E402

REPO = Path(__file__).resolve().parents[2]

ALCHEMY_HTTP = "https://arb-mainnet.g.alchemy.com/v2/KEY"
ALCHEMY_WS = "wss://arb-mainnet.g.alchemy.com/v2/KEY"


class _ScriptedPrompt:
    """Feeds queued answers to the wizard and records what it was asked."""

    def __init__(self, *answers: str):
        self.answers = list(answers)
        self.asked: list[str] = []

    def __call__(self, message: str) -> str:
        self.asked.append(message)
        return self.answers.pop(0) if self.answers else "q"


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.lo = Layout(Path(self._tmp.name))
        self.store = credentials.open_store(self.lo)
        self.addCleanup(self.store.close)
        src = REPO / "ingestion" / "config" / "pools"
        dst = self.lo.ingestion / "config" / "pools"
        dst.mkdir(parents=True, exist_ok=True)
        for path in src.glob("*.example.toml"):
            shutil.copyfile(path, dst / path.name)


class ProgressBarTest(unittest.TestCase):
    def test_endpoints(self) -> None:
        self.assertIn("0.0%", wizard.progress_bar(0))
        self.assertIn("100.0%", wizard.progress_bar(100))
        self.assertIn("█", wizard.progress_bar(100))
        self.assertIn("░", wizard.progress_bar(0))


class AutofillTest(_Base):
    def test_websocket_url_is_derived_from_the_https_one(self) -> None:
        """The operator should never be asked for a value the app can work out."""
        self.store.set("rpc.arbitrum.http", ALCHEMY_HTTP)
        report = healthcheck.run(self.lo, self.store, probe=False, env={}, check_build_state=False)
        self.assertEqual(wizard.autofill(report, self.store), ["rpc.arbitrum.ws"])
        self.assertEqual(self.store.get("rpc.arbitrum.ws"), ALCHEMY_WS)

    def test_infura_style_derivation(self) -> None:
        self.store.set("rpc.base.http", "https://base-mainnet.infura.io/v3/KEY")
        report = healthcheck.run(
            self.lo, self.store, probe=False, env={}, chains=["base"], check_build_state=False
        )
        wizard.autofill(report, self.store)
        self.assertEqual(self.store.get("rpc.base.ws"), "wss://base-mainnet.infura.io/ws/v3/KEY")

    def test_nothing_to_derive_without_the_source_value(self) -> None:
        report = healthcheck.run(self.lo, self.store, probe=False, env={}, check_build_state=False)
        self.assertEqual(wizard.autofill(report, self.store), [])

    def test_an_already_satisfied_value_is_not_overwritten(self) -> None:
        self.store.set("rpc.arbitrum.http", ALCHEMY_HTTP)
        self.store.set("rpc.arbitrum.ws", "wss://my-own-node:8546")
        report = healthcheck.run(self.lo, self.store, probe=False, env={}, check_build_state=False)
        self.assertEqual(wizard.autofill(report, self.store), [])
        self.assertEqual(self.store.get("rpc.arbitrum.ws"), "wss://my-own-node:8546")


class AskTest(unittest.TestCase):
    def setUp(self) -> None:
        self.req = requirements.by_key(["arbitrum"])["rpc.arbitrum.http"]

    def test_accepts_a_valid_answer(self) -> None:
        self.assertEqual(wizard.ask(self.req, default=None, prompt=_ScriptedPrompt(ALCHEMY_HTTP)), ALCHEMY_HTTP)

    def test_reprompts_until_the_answer_validates(self) -> None:
        prompt = _ScriptedPrompt("not-a-url", "also bad", ALCHEMY_HTTP)
        self.assertEqual(wizard.ask(self.req, default=None, prompt=prompt), ALCHEMY_HTTP)
        self.assertEqual(len(prompt.asked), 3)

    def test_enter_keeps_the_default(self) -> None:
        self.assertEqual(wizard.ask(self.req, default=ALCHEMY_HTTP, prompt=_ScriptedPrompt("")), ALCHEMY_HTTP)

    def test_enter_with_no_default_reprompts_rather_than_storing_nothing(self) -> None:
        prompt = _ScriptedPrompt("", ALCHEMY_HTTP)
        self.assertEqual(wizard.ask(self.req, default=None, prompt=prompt), ALCHEMY_HTTP)

    def test_skip_returns_none(self) -> None:
        self.assertIsNone(wizard.ask(self.req, default=None, prompt=_ScriptedPrompt("s")))

    def test_quit_raises(self) -> None:
        with self.assertRaises(wizard._Quit):
            wizard.ask(self.req, default=None, prompt=_ScriptedPrompt("q"))

    def test_a_number_picks_the_public_endpoint_suggestion(self) -> None:
        picked = wizard.ask(self.req, default=None, prompt=_ScriptedPrompt("1"))
        self.assertEqual(picked, requirements.CHAINS["arbitrum"].public_http)

    def test_api_keys_are_read_without_echo(self) -> None:
        """A short typed secret is shoulder-surfable, so it is read hidden —
        unlike a long pasted URL, where hiding it would make a typo invisible."""
        req = requirements.BLOCKSCOUT_API_KEY
        self.assertTrue(wizard._hide_input(req))
        with mock.patch("getpass.getpass", return_value="secretkey") as getpass_mock:
            self.assertEqual(wizard.ask(req, default=None, prompt=_ScriptedPrompt()), "secretkey")
        getpass_mock.assert_called_once()

    def test_urls_are_not_hidden(self) -> None:
        self.assertFalse(wizard._hide_input(self.req))


class ChooseChainsTest(_Base):
    def test_enter_keeps_the_current_selection(self) -> None:
        self.assertEqual(wizard.choose_chains(self.store, _ScriptedPrompt("")), ["arbitrum"])

    def test_numbers_select_chains(self) -> None:
        self.assertEqual(wizard.choose_chains(self.store, _ScriptedPrompt("1,2")), ["arbitrum", "base"])

    def test_selection_is_persisted(self) -> None:
        wizard.choose_chains(self.store, _ScriptedPrompt("2"))
        self.assertEqual(healthcheck.selected_chains(self.store), ["base"])

    def test_names_work_too(self) -> None:
        self.assertEqual(wizard.choose_chains(self.store, _ScriptedPrompt("base,ink")), ["base", "ink"])

    def test_result_is_in_canonical_order_regardless_of_input_order(self) -> None:
        self.assertEqual(wizard.choose_chains(self.store, _ScriptedPrompt("5,1")), ["arbitrum", "ink"])

    def test_junk_is_ignored_and_falls_back_to_current(self) -> None:
        self.assertEqual(wizard.choose_chains(self.store, _ScriptedPrompt("99,nonsense")), ["arbitrum"])


class RunWizardTest(_Base):
    def _run(self, *answers: str, **kw):
        kw.setdefault("probe", False)
        kw.setdefault("ask_chains", False)
        kw.setdefault("interactive", True)
        # Explicit empty environment: this sandbox (and any developer machine)
        # can carry real L2ARB__CHAINS__* / PROFIT_RECEIVER values, which would
        # otherwise satisfy the check before the wizard ever asked anything and
        # make these tests pass or fail on ambient state rather than behaviour.
        kw.setdefault("env", {})
        return wizard.run_wizard(self.lo, self.store, prompt=_ScriptedPrompt(*answers), **kw)

    def test_one_pasted_endpoint_reaches_one_hundred_percent(self) -> None:
        """The headline flow: paste one URL, everything else is derived or
        already verified, and the score hits 100%."""
        report = self._run(ALCHEMY_HTTP)
        self.assertTrue(report.is_complete)
        self.assertEqual(report.percent, 100.0)
        self.assertEqual(self.store.get("rpc.arbitrum.http"), ALCHEMY_HTTP)
        self.assertEqual(self.store.get("rpc.arbitrum.ws"), ALCHEMY_WS)

    def test_reaching_one_hundred_writes_a_valid_utf8_config(self) -> None:
        self._run(ALCHEMY_HTTP)
        self.assertTrue(self.lo.config_toml.exists())
        self.assertTrue(textio.is_valid_utf8(self.lo.config_toml))
        body = textio.read_text(self.lo.config_toml)
        self.assertIn(ALCHEMY_HTTP, body)
        self.assertIn('name          = "arbitrum"', body)

    def test_the_written_config_has_no_placeholders_left(self) -> None:
        from l2arb import config as config_mod

        self._run(ALCHEMY_HTTP)
        self.assertTrue(config_mod.config_is_live_ready(self.lo))

    def test_an_already_complete_install_asks_nothing(self) -> None:
        self.store.set("rpc.arbitrum.http", ALCHEMY_HTTP)
        self.store.set("rpc.arbitrum.ws", ALCHEMY_WS)
        prompt = _ScriptedPrompt()
        report = wizard.run_wizard(
            self.lo, self.store, prompt=prompt, probe=False, ask_chains=True, interactive=True, env={}
        )
        self.assertTrue(report.is_complete)
        self.assertEqual(prompt.asked, [], "a configured install must go straight through")

    def test_quitting_keeps_what_was_already_entered(self) -> None:
        report = self._run(ALCHEMY_HTTP, "q")
        self.assertEqual(self.store.get("rpc.arbitrum.http"), ALCHEMY_HTTP)
        self.assertIsNotNone(report)

    def test_skipping_everything_does_not_loop_forever(self) -> None:
        """A user who skips every box must fall out of the wizard, not be
        trapped in it."""
        report = self._run(*(["s"] * 12))
        self.assertFalse(report.is_complete)

    def test_incomplete_run_writes_no_config(self) -> None:
        self._run("s", "s", "s", "s", "s", "s")
        self.assertFalse(self.lo.config_toml.exists())

    def test_non_interactive_never_prompts(self) -> None:
        prompt = _ScriptedPrompt()
        report = wizard.run_wizard(
            self.lo, self.store, prompt=prompt, probe=False, interactive=False, env={}
        )
        self.assertEqual(prompt.asked, [])
        self.assertFalse(report.is_complete)

    def test_the_run_is_recorded_in_the_database(self) -> None:
        self._run(ALCHEMY_HTTP)
        last = self.store.last_health()
        assert last is not None
        self.assertEqual(last["percent"], 100.0)

    def test_recommended_values_are_offered_after_the_required_ones(self) -> None:
        """Reaching 100% ends the *required* list, not the walk — the API key
        and wallet address are still offered, since being prompted for them is
        the whole point of a guided setup."""
        wallet = "0x50A71dF7DfC5850e8434C7c8A564366F4980183b"
        # The Blockscout key is read hidden (getpass), so it is not fed by the
        # scripted prompt; skipping it there advances to the wallet box.
        with mock.patch("getpass.getpass", return_value="s"):
            self._run(ALCHEMY_HTTP, wallet)
        self.assertEqual(self.store.get("wallet.profit_receiver"), wallet)

    def test_a_hidden_api_key_prompt_stores_the_key(self) -> None:
        with mock.patch("getpass.getpass", return_value="blockscout-key-123"):
            self._run(ALCHEMY_HTTP, "s")
        self.assertEqual(self.store.get("engine.blockscout_api_key"), "blockscout-key-123")

    def test_a_box_that_can_never_be_satisfied_gives_up_instead_of_hanging(self) -> None:
        """A non-TTY `getpass` returning empty forever must degrade to a skip,
        not spin the process."""
        req = requirements.by_key(["arbitrum"])["rpc.arbitrum.http"]
        prompt = _ScriptedPrompt(*(["still not a url"] * (wizard.MAX_ATTEMPTS + 5)))
        self.assertIsNone(wizard.ask(req, default=None, prompt=prompt))
        self.assertLessEqual(len(prompt.asked), wizard.MAX_ATTEMPTS)

    def test_optional_tuning_is_not_prompted_by_default(self) -> None:
        prompt = _ScriptedPrompt(ALCHEMY_HTTP, "s", "s", "s", "s")
        wizard.run_wizard(
            self.lo, self.store, prompt=prompt, probe=False, ask_chains=False, interactive=True, env={}
        )
        self.assertIsNone(self.store.get("engine.max_hops"))

    def test_two_chains_both_get_written(self) -> None:
        self.store.set(requirements.SELECTED_CHAINS_KEY, "arbitrum,base")
        report = self._run(ALCHEMY_HTTP, "https://base-mainnet.g.alchemy.com/v2/KEY")
        self.assertTrue(report.is_complete)
        body = textio.read_text(self.lo.config_toml)
        self.assertIn('name          = "arbitrum"', body)
        self.assertIn('name          = "base"', body)


class InputBoxTest(unittest.TestCase):
    def _draw(self, req) -> list[str]:
        import contextlib
        import io

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            wizard.input_box(req, default=None, prompt=lambda _m: "")
        return [line for line in buffer.getvalue().splitlines() if line.strip()]

    def test_box_borders_line_up(self) -> None:
        """The title sits inline in the top border, so its width has to be
        computed against the content rows or the box renders visibly ragged."""
        for key in ("rpc.arbitrum.http", "wallet.profit_receiver"):
            req = requirements.by_key(["arbitrum"])[key]
            lines = self._draw(req)
            widths = {len(line) for line in lines}
            self.assertEqual(len(widths), 1, f"{key}: ragged box, widths={widths}")

    def test_box_lists_the_escape_hatches(self) -> None:
        text = "\n".join(self._draw(requirements.by_key(["arbitrum"])["rpc.arbitrum.http"]))
        self.assertIn("[s] skip", text)
        self.assertIn("[q]", text)


class RenderTest(_Base):
    def test_render_report_masks_secrets(self) -> None:
        import contextlib
        import io

        self.store.set("rpc.arbitrum.http", "https://host/v2/TOPSECRET", is_secret=True)
        report = healthcheck.run(self.lo, self.store, probe=False, env={}, check_build_state=False)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            wizard.render_report(report)
        self.assertNotIn("TOPSECRET", buffer.getvalue())

    def test_explain_prints_all_four_guidance_sections(self) -> None:
        import contextlib
        import io

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            wizard.explain(requirements.by_key(["arbitrum"])["rpc.arbitrum.http"])
        out = buffer.getvalue()
        for heading in ("WHAT IT IS", "WHY THIS APP NEEDS IT", "WHERE TO GET IT", "WHAT A CORRECT ANSWER LOOKS LIKE"):
            self.assertIn(heading, out)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
