"""The guided setup: walk the operator to a 100%-healthy install, one box at a time.

Runs on every launch. It shows the health report, then for each thing still
missing prints the full explanation from :mod:`l2arb.requirements` — what the
value is, why the app needs it, where to get it, what a correct one looks like —
and opens an input box for it. Answers are validated immediately, stored in the
SQLite credential database, and the health check re-runs until it reads 100%.

Design rules this follows, learned from the failure that prompted it:

* **Never ask for something the app can work out.** A WebSocket URL is derived
  from the HTTPS one already given and silently accepted if it probes clean; the
  operator only ever sees that box if the derivation actually failed.
* **Never ask for something that isn't a credential.** Pool, token and DEX
  addresses are on-chain facts with verified shipped copies — the check
  materialises them and moves on. Typing one in by hand is how fabricated market
  data gets into a bot.
* **Never block on an optional value.** Everything is skippable; only blocking
  items hold the percentage below 100.
* **Never hang a non-interactive launch.** With no TTY the wizard prints exactly
  what is missing and how to supply it, then returns.
"""

from __future__ import annotations

import getpass
import sys
from typing import Callable

from . import console, credentials, healthcheck, requirements, setup as setup_mod
from .credentials import CredentialStore
from .healthcheck import HealthReport, ItemResult
from .paths import Layout
from .requirements import BLOCKING, OPTIONAL, RECOMMENDED, Requirement

Prompt = Callable[[str], str]

#: Typed at any box to skip this one value / abandon the wizard.
SKIP_TOKENS = frozenset({"s", "skip"})
QUIT_TOKENS = frozenset({"q", "quit", "exit"})

_TIER_LABEL = {
    BLOCKING: "REQUIRED",
    RECOMMENDED: "recommended",
    OPTIONAL: "optional",
}

_STATUS_MARK = {
    healthcheck.OK: "✓",
    healthcheck.MISSING: "✗",
    healthcheck.INVALID: "✗",
    healthcheck.UNREACHABLE: "!",
    healthcheck.WRONG_CHAIN: "!",
    # Satisfied, but with a caveat the detail line spells out — so it is marked
    # apart from a clean "✓" rather than either hidden or shown as a failure.
    healthcheck.RATE_LIMITED: "~",
}


# ── Rendering ────────────────────────────────────────────────────────────────


def progress_bar(percent: float, width: int = 28) -> str:
    filled = int(round(width * percent / 100.0))
    return f"[{'█' * filled}{'░' * (width - filled)}] {percent:5.1f}%"


def render_report(report: HealthReport) -> None:
    """Print the full health picture: score, then every check and its state."""
    console.banner("Health check")
    console.info(f"networks : {', '.join(report.chains)}")
    console.info(
        f"score    : {progress_bar(report.percent)}  "
        f"({report.satisfied_blocking}/{report.total_blocking} required checks)"
    )
    if not report.probed:
        console.info("           (endpoints not probed this run — format checked only)")

    by_tier: dict[str, list[ItemResult]] = {BLOCKING: [], RECOMMENDED: [], OPTIONAL: []}
    for item in report.items:
        by_tier[item.requirement.tier].append(item)

    for tier in (BLOCKING, RECOMMENDED, OPTIONAL):
        rows = by_tier[tier]
        if not rows:
            continue
        print()
        console.info(f"── {_TIER_LABEL[tier]} ──")
        for item in rows:
            mark = _STATUS_MARK.get(item.status, "?")
            line = f"  {mark} {item.requirement.title}"
            if item.satisfied:
                line += f"  =  {item.display_value()}"
                if item.source != healthcheck.SRC_DB:
                    line += f"  (from {item.source})"
            else:
                line += f"  — {item.status}"
            print(line)
            if item.detail:
                console.info(f"      {item.detail}")

    print()
    console.info("── pool data (verified on-chain, not entered by hand) ──")
    for pool in report.pools:
        if pool.satisfied:
            print(f"  ✓ {pool.chain}: {pool.pool_count} pool(s)")
        else:
            print(f"  ✗ {pool.chain}: {pool.status}")
            if pool.detail:
                console.info(f"      {pool.detail}")

    print()
    console.info("── build state (the app builds these itself) ──")
    build = report.build
    for name, built in (
        ("dashboard", build.dashboard_built),
        ("engine", build.engine_built),
        ("ingestion", build.ingestion_built),
    ):
        print(f"  {'✓' if built else '·'} {name}: {'built' if built else 'not built yet'}")
    if not build.ready:
        console.info("      run `l2arb install` (or just relaunch) to build what is missing")


def explain(req: Requirement) -> None:
    """Print the full teaching block for one requirement."""
    print()
    console.banner(req.title)
    console.info(f"[{_TIER_LABEL[req.tier]}]")
    print()
    print("  WHAT IT IS")
    for line in _wrap(req.what):
        print(f"    {line}")
    print()
    print("  WHY THIS APP NEEDS IT")
    for line in _wrap(req.why):
        print(f"    {line}")
    print()
    print("  WHERE TO GET IT")
    for line in req.where:
        print(f"    {line}")
    print()
    print("  WHAT A CORRECT ANSWER LOOKS LIKE")
    for line in req.looks_like.splitlines():
        print(f"    {line}")
    if req.env_var:
        print()
        console.info(f"(stored in the local database; also readable from ${req.env_var})")


def _wrap(text: str, width: int = 76) -> list[str]:
    import textwrap

    return textwrap.wrap(" ".join(text.split()), width=width) or [""]


def input_box(req: Requirement, *, default: str | None, prompt: Prompt) -> str:
    """Draw the labelled input box and return the raw line the user typed."""
    options = []
    for index, (_value, label) in enumerate(req.suggestions, start=1):
        options.append(f"[{index}] {label}")
    if default:
        shown = credentials.mask_url(default) if req.is_secret and "://" in default else default
        options.append(f"[Enter] keep {shown}")
    options.append("[s] skip")
    options.append("[q] finish later")

    width = max(len(req.title) + 4, *(len(o) for o in options), 40)
    # The top border carries the title inline: "┌─ <title> ────┐". Its inner
    # span must equal the content rows' ("│ <opt padded to width> │" = width+2),
    # and "─ <title> " already occupies len(title)+3 of it.
    heading = f"─ {req.title} "
    print()
    print("  ┌" + heading + "─" * max(0, width + 2 - len(heading)) + "┐")
    for opt in options:
        print(f"  │ {opt.ljust(width)} │")
    print("  └" + "─" * (width + 2) + "┘")
    return prompt("   > ")


def _hide_input(req: Requirement) -> bool:
    """Whether to read this value without echoing it.

    API keys are hidden — they are short, usually typed, and shoulder-surfable.
    URLs are not: they are pasted, and hiding a long pasted string makes a typo
    impossible to spot, which costs more than the marginal secrecy of a value
    that is about to be written to a config file anyway.
    """
    return req.is_secret and req.category == requirements.CAT_API_KEY


#: How many rejected answers before a box gives up and moves on. Bounded rather
#: than "until it validates" so a stdin that can never satisfy the prompt — a
#: hidden `getpass` read on a non-TTY returning empty forever, a piped input that
#: has run dry — degrades into a skip instead of spinning the process forever.
MAX_ATTEMPTS = 8


def ask(req: Requirement, *, default: str | None, prompt: Prompt) -> str | None:
    """Ask for one value until it validates. Returns the accepted value, or
    ``None`` for skip; raises :class:`_Quit` when the user asks to stop."""
    for _attempt in range(MAX_ATTEMPTS):
        if _hide_input(req):
            input_box(req, default=default, prompt=lambda _m: "")
            try:
                raw = getpass.getpass("   > (hidden) ")
            except (EOFError, KeyboardInterrupt) as exc:
                raise _Quit from exc
        else:
            raw = input_box(req, default=default, prompt=prompt)

        answer = raw.strip()
        lowered = answer.lower()
        if lowered in QUIT_TOKENS:
            raise _Quit
        if lowered in SKIP_TOKENS:
            return None
        if not answer:
            if default:
                return default
            console.warn("nothing entered — type a value, [s] to skip, or [q] to finish later")
            continue
        if answer.isdigit() and 1 <= int(answer) <= len(req.suggestions):
            answer = req.suggestions[int(answer) - 1][0]

        error = req.validate(answer)
        if error:
            console.err(f"that value won't work: {error}")
            continue
        return answer

    console.warn(f"no usable answer after {MAX_ATTEMPTS} tries — skipping {req.title} for now")
    return None


class _Quit(Exception):
    """The operator asked to stop entering values."""


# ── Chain selection ──────────────────────────────────────────────────────────


def choose_chains(store: CredentialStore, prompt: Prompt) -> list[str]:
    """Ask which networks to watch. Each one adds an endpoint to supply, so this
    is asked up front rather than making the operator configure all five to
    reach 100%."""
    current = healthcheck.selected_chains(store)
    console.banner("Which networks should the bot watch?")
    console.info("Each network you pick needs one RPC endpoint from you.")
    console.info("Start with one — you can add more later by re-running `l2arb setup`.")
    print()
    for index, key in enumerate(requirements.CHAIN_ORDER, start=1):
        info = requirements.CHAINS[key]
        mark = "•" if key in current else " "
        extra = "   (recommended to start: deepest shipped pool data)" if key == "arbitrum" else ""
        print(f"   {mark} {index}. {info.display} (chain id {info.chain_id}){extra}")
    print()
    console.info(f"Currently selected: {', '.join(current)}")
    raw = prompt("   Numbers, comma-separated (Enter to keep current): ").strip()
    if not raw:
        return current

    picked: list[str] = []
    for token in raw.replace(" ", "").split(","):
        if token.isdigit() and 1 <= int(token) <= len(requirements.CHAIN_ORDER):
            picked.append(requirements.CHAIN_ORDER[int(token) - 1])
        elif token.lower() in requirements.CHAINS:
            picked.append(token.lower())
        elif token:
            console.warn(f"ignoring {token!r} — not one of the numbers above")
    chosen = [c for c in requirements.CHAIN_ORDER if c in set(picked)] or current
    store.set(
        requirements.SELECTED_CHAINS_KEY,
        ",".join(chosen),
        category="chains",
        source="wizard",
    )
    return chosen


# ── Auto-fill ────────────────────────────────────────────────────────────────


def autofill(report: HealthReport, store: CredentialStore) -> list[str]:
    """Fill in everything derivable from what is already known, before asking.

    Today that is the WebSocket URL, derived from the chain's HTTPS endpoint via
    the same provider-aware heuristic ``l2arb setup`` already uses. Returns the
    keys filled, so the caller knows to re-run the check.
    """
    known = {i.key: i.value for i in report.items if i.satisfied and i.value}
    filled: list[str] = []
    for item in report.items:
        req = item.requirement
        if item.satisfied or not req.derived_from:
            continue
        source_value = known.get(req.derived_from)
        if not source_value:
            continue
        derived = setup_mod.derive_ws_url(source_value)
        if not derived or req.validate(derived):
            continue
        store.set(req.key, derived, category=req.category, is_secret=req.is_secret, source="derived")
        filled.append(req.key)
    return filled


# ── The wizard ───────────────────────────────────────────────────────────────


def _is_interactive() -> bool:
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (ValueError, AttributeError):  # pragma: no cover - closed/odd stdin
        return False


def _report_non_interactive(report: HealthReport) -> None:
    console.warn("not running in a terminal, so the guided setup cannot open an input box.")
    console.info("Run `l2arb setup` from a terminal, or set these environment variables:")
    for item in report.unsatisfied(include_recommended=False):
        env = item.requirement.env_var or "(no env var)"
        console.info(f"  {env}  — {item.requirement.title}")


def run_wizard(
    lo: Layout,
    store: CredentialStore,
    *,
    prompt: Prompt = input,
    probe: bool = True,
    include_optional: bool = False,
    ask_chains: bool = True,
    interactive: bool | None = None,
    env: dict[str, str] | None = None,
) -> HealthReport:
    """Drive the install to 100%, then write the config. Returns the final report.

    ``env`` overrides the process environment the check resolves against;
    production callers leave it ``None`` (meaning "the real environment", so an
    operator's exported RPC URL is honoured) and tests pass an explicit mapping
    so their result never depends on what happens to be set on the machine.
    """
    if interactive is None:
        interactive = _is_interactive()

    def check(chains: list[str] | None = None) -> HealthReport:
        return healthcheck.run(lo, store, probe=probe, chains=chains, env=env)

    # Check *before* asking anything. On a launch where everything is already
    # configured — the common case once set up — the operator must not be made
    # to re-answer "which networks?" just to get to their dashboard.
    report = check()
    if autofill(report, store):
        report = check(report.chains)

    if report.is_complete:
        render_report(report)
        console.ok("every required value is present — nothing to ask for.")
        _finish(lo, store, report)
        return report

    render_report(report)

    if not interactive:
        _report_non_interactive(report)
        return report

    if ask_chains:
        chains = choose_chains(store, prompt)
        report = check(chains)
        if autofill(report, store):
            report = check(report.chains)
        if report.is_complete:
            render_report(report)
            _finish(lo, store, report)
            return report

    console.banner("Let's fill in what's missing")
    console.info("For each item: what it is, why it's needed, where to get it, then a box to type it in.")
    console.info("Press [s] to skip one, or [q] to stop and come back later.")

    try:
        report = _collect(
            lo, store, report, prompt=prompt, check=check, include_optional=include_optional
        )
    except _Quit:
        console.warn("stopped — everything you entered so far is saved.")
        report = check(report.chains)

    render_report(report)
    _finish(lo, store, report)
    return report


def _collect(
    lo: Layout,
    store: CredentialStore,
    report: HealthReport,
    *,
    prompt: Prompt,
    check: Callable[..., HealthReport],
    include_optional: bool,
) -> HealthReport:
    """Ask for each unsatisfied value, re-checking as we go.

    Bounded by the number of requirements rather than looping until complete: a
    value the operator keeps skipping, or an endpoint that stays unreachable,
    must not trap them in an infinite prompt.
    """
    asked: set[str] = set()
    announced_complete = False
    for _round in range(len(report.items) + 1):
        pending = [
            item
            for item in report.unsatisfied(include_recommended=True)
            if item.key not in asked
            and (include_optional or item.requirement.tier != OPTIONAL)
        ]
        if not pending:
            break

        # Reaching 100% ends the *required* list, not the walk: the API key and
        # wallet address below are exactly the sort of thing an operator wants
        # to be offered rather than left to discover. They are all skippable,
        # and the score is already 100 regardless of how they are answered.
        if report.is_complete and not announced_complete:
            announced_complete = True
            console.ok("all required values are in — health is 100%.")
            console.info("What follows is optional; press [s] to skip any of it, or [q] to finish now.")

        item = pending[0]
        req = item.requirement
        asked.add(req.key)

        if item.status in (healthcheck.UNREACHABLE, healthcheck.WRONG_CHAIN, healthcheck.INVALID):
            console.warn(f"{req.title}: {item.detail}")
        explain(req)

        current = item.value if item.source != healthcheck.SRC_NONE else None
        keep = current if item.satisfied else None
        answer = ask(req, default=keep or req.default, prompt=prompt)
        if answer is None:
            console.info(f"skipped {req.title}")
        else:
            store.set(
                req.key,
                answer,
                category=req.category,
                is_secret=req.is_secret,
                source="wizard",
            )
            console.ok(f"saved {req.title}")
            if autofill(check(report.chains), store):
                console.info("  (derived the matching WebSocket URL for you)")

        report = check(report.chains)
        if not report.is_complete:
            console.info(f"health now: {progress_bar(report.percent)}")
    return report


def _finish(lo: Layout, store: CredentialStore, report: HealthReport) -> None:
    """Persist the run and regenerate config.toml from what is now stored."""
    store.record_health(
        mode="live" if report.is_complete else "partial",
        percent=report.percent,
        satisfied=report.satisfied_blocking,
        total=report.total_blocking,
    )

    if not report.is_complete:
        console.warn(
            f"health is {report.percent:.0f}% — the bot will run in safe paper mode until it reaches 100%."
        )
        console.info("Re-run `l2arb setup` any time to finish; nothing you entered is lost.")
        return

    written = setup_mod.write_config_from_store(lo, store, report.chains)
    if written is None:
        console.err("could not write config.toml — see the message above")
        return
    console.ok(f"wrote {written}")
    ok, note = setup_mod.validate_config(lo)
    (console.ok if ok else console.warn)(note)
    console.banner("You're ready")
    console.info("Everything required is configured. Start the live stack with:  l2arb run --live")
