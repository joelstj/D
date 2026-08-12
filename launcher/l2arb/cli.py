"""Command-line entry point for the L2 Arbitrage Bot launcher.

Subcommands:
  doctor            report toolchains, install state, and config readiness
  health            check every endpoint, key and setting; print a 0–100% score
  install           build the components (``--paper-only`` for dashboard alone)
  setup             guided setup — health-check everything, then explain and
                    prompt for each missing value until the score reads 100%
                    (``--quick`` for the one-URL Arbitrum path, ``--all-chains``
                    for the per-chain endpoint walk)
  run               start the stack (``--live`` for the full on-chain path)
  auto  (default)   install-if-needed, health-check, guide any fixes, then run

Running with **no subcommand** (e.g. double-clicking the .exe) performs ``auto``:
if the app is not installed it installs everything it can, then **runs the full
health check on every launch** — every environment variable, secret, RPC
endpoint, WebSocket URL and API key — walking the operator through anything
missing before launching the dashboard and opening the browser. The gate never
blocks: it is skippable at every prompt and degrades to safe paper mode, so an
incomplete config explains itself instead of failing in a restart loop.
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback

from . import (
    __version__,
    console,
    config,
    credentials,
    healthcheck,
    installer,
    payload,
    prereqs,
    run,
    setup,
    state,
    wizard,
)
from .paths import IS_WINDOWS, is_frozen, layout


def _add_run_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("--live", action="store_true", help="run the full on-chain stack (needs a filled config.toml)")
    p.add_argument("--paper", action="store_true", help="force paper/simulation mode")
    p.add_argument("--no-browser", action="store_true", help="do not open a browser window")
    # default=None so `cmd_run` can tell "not passed" from "passed 8787" and fall
    # back to the port stored by the guided setup before the built-in default.
    p.add_argument("--port", type=int, default=None, help=f"dashboard port (default {config.DASHBOARD_PORT})")
    p.add_argument("--no-probe", action="store_true", help="skip the network probes in the health check")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="l2arb", description="L2 Arbitrage Flash-Loan Bot launcher")
    parser.add_argument("--version", action="version", version=f"l2arb-launcher {__version__}")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("doctor", help="report toolchains + install/config state")

    p_install = sub.add_parser("install", help="build the components")
    p_install.add_argument("--paper-only", action="store_true", help="build only the dashboard (no engine/ingestion)")
    p_install.add_argument("--skip-engine", action="store_true")
    p_install.add_argument("--skip-ingestion", action="store_true")

    p_health = sub.add_parser("health", help="check every endpoint, key and setting; report a score")
    p_health.add_argument("--no-probe", action="store_true", help="check formats only; do not call the network")

    p_setup = sub.add_parser("setup", help="guided setup: prompts for everything still missing")
    p_setup.add_argument("--quick", action="store_true", help="the one-URL Arbitrum quick-start instead of the guided walk")
    p_setup.add_argument("--optional", action="store_true", help="also prompt for optional tuning values")
    p_setup.add_argument("--no-probe", action="store_true", help="skip the network probes while checking")
    p_setup.add_argument("--provider", help="RPC provider preset (alchemy | infura)")
    p_setup.add_argument("--key", help="your RPC provider API key (used with --provider)")
    p_setup.add_argument("--http", help="Arbitrum HTTPS RPC URL (skips the prompt)")
    p_setup.add_argument("--ws", help="Arbitrum WebSocket URL (default: derived from --http)")
    p_setup.add_argument("--backup", help="backup HTTPS URL appended for rate-limit failover")
    p_setup.add_argument(
        "--all-chains",
        action="store_true",
        help="guided setup for every chain (arbitrum/base/optimism/unichain/ink): "
        "auto-detects RPC endpoints already in your environment, prompts individually "
        "for the rest, and auto-discovers real pools on-chain where possible",
    )

    p_run = sub.add_parser("run", help="start the stack")
    _add_run_flags(p_run)

    p_auto = sub.add_parser("auto", help="install-if-needed then run (default)")
    _add_run_flags(p_auto)
    p_auto.add_argument("--paper-only", action="store_true", help="if installing, build only the dashboard")
    p_auto.add_argument("--no-setup", action="store_true", help="do not open the guided setup even if values are missing")

    return parser


def cmd_doctor(lo) -> int:
    prereqs.report(prereqs.detect_all())
    ready = state.probe(lo)
    console.banner("Install state")
    console.info(f"workspace  : {lo.root}")
    console.info(f"frozen exe : {is_frozen()}")
    console.info(f"dashboard  : {'ready' if ready.dashboard else 'not built'}")
    console.info(f"engine     : {'ready' if ready.engine else 'not built'}")
    console.info(f"ingestion  : {'ready' if ready.ingestion else 'not built'}")
    console.info(f"config.toml: {lo.config_toml if lo.config_toml.exists() else 'not generated'}")
    live_ready = config.config_is_live_ready(lo)
    console.info(f"live-ready : {live_ready}")

    with credentials.store(lo) as st:
        console.info(f"credentials: {credentials.db_path(lo)}")
        report = healthcheck.run(lo, st, probe=False)
        console.info(
            f"health     : {report.percent:.0f}% "
            f"({report.satisfied_blocking}/{report.total_blocking} required, format-checked only)"
        )
    console.step("Next: " + state.next_step(ready, live_ready))
    if not live_ready:
        console.step("Or run `l2arb setup` for the guided walk-through of everything still missing.")
    return 0


def cmd_health(lo, args) -> int:
    """Full health check; exit 0 only when every required value is satisfied."""
    with credentials.store(lo) as st:
        report = healthcheck.run(lo, st, probe=not getattr(args, "no_probe", False))
        wizard.render_report(report)
        st.record_health(
            mode="check",
            percent=report.percent,
            satisfied=report.satisfied_blocking,
            total=report.total_blocking,
        )
        if not report.is_complete:
            console.step("Run `l2arb setup` to fill in what's missing, one prompt at a time.")
        return 0 if report.is_complete else 1


def cmd_install(lo, args) -> int:
    opts = installer.InstallOptions(
        paper_only=getattr(args, "paper_only", False),
        skip_engine=getattr(args, "skip_engine", False),
        skip_ingestion=getattr(args, "skip_ingestion", False),
    )
    config.ensure_config_toml(lo)
    ok = installer.install(lo, opts)
    return 0 if ok else 1


def cmd_setup(lo, args) -> int:
    """Guided setup by default; the older single-purpose flows stay available.

    The guided walk is the default because it is the only path that knows what
    a *complete* install looks like — it health-checks every value, explains and
    prompts for each one still missing, and stops when the score reads 100%.
    ``--quick`` (one Arbitrum URL) and ``--all-chains`` remain for operators who
    already know exactly what they want to write.
    """
    if getattr(args, "all_chains", False):
        return setup.run_setup_all_chains(lo)
    if getattr(args, "quick", False):
        return setup.run_setup(lo, args)
    with credentials.store(lo) as st:
        report = wizard.run_wizard(
            lo,
            st,
            probe=not getattr(args, "no_probe", False),
            include_optional=getattr(args, "optional", False),
        )
        return 0 if report.is_complete else 1


def _resolve_port(lo, args) -> int:
    """`--port` wins; then the port stored by the guided setup; then the default."""
    if getattr(args, "port", None) is not None:
        return args.port
    with credentials.store(lo) as st:
        stored = st.get("dashboard.port")
    if stored and stored.strip().isdigit():
        return int(stored.strip())
    return config.DASHBOARD_PORT


def _effective_live(lo, args) -> bool:
    if getattr(args, "paper", False):
        return False
    ready = state.probe(lo)
    if getattr(args, "live", False):
        return True  # run() re-validates and downgrades if it can't honour it
    # auto: go live only if everything is genuinely ready.
    return ready.full and config.config_is_live_ready(lo)


def cmd_run(lo, args) -> int:
    return run.run(
        lo,
        live=_effective_live(lo, args),
        port=_resolve_port(lo, args),
        open_browser=not args.no_browser,
    )


def _welcome() -> None:
    console.banner("Welcome to the L2 Arbitrage Bot")
    console.info("This first run installs everything it needs and opens the dashboard.")
    console.info("It starts in SAFE paper/simulation mode — it never sends a real")
    console.info("transaction or touches your funds. To watch real on-chain data later,")
    console.info("run `l2arb setup` (one RPC URL, Arbitrum only) or `l2arb setup --all-chains`")
    console.info("(every chain, auto-detected/prompted individually), then `l2arb run --live`.")
    console.info("First install needs internet and a few minutes; later runs are instant.")


def _debug_enabled() -> bool:
    """True when the operator asked to see tracebacks (``L2ARB_DEBUG=1``)."""
    return os.environ.get("L2ARB_DEBUG", "").strip() not in ("", "0", "false", "False")


def _health_gate(lo, args) -> None:
    """Run the full health check on **every** launch, and guide the fixes.

    This is the behaviour the ``.exe`` is expected to have: each time it starts,
    re-verify every environment variable, secret, RPC endpoint, WebSocket URL
    and API key the stack needs — because any of them can rot between runs (a
    key revoked, a free-tier endpoint expired, a variable dropped from the
    shell) and the previous failure mode was a silent restart loop rather than
    an explanation.

    When something is missing it walks the operator through it. It never blocks:
    the wizard is skippable at every box, a non-interactive launch just prints
    what is needed, and either way the run continues into safe paper mode, so an
    incomplete config degrades instead of refusing to start.
    """
    if getattr(args, "no_setup", False):
        return
    try:
        with credentials.store(lo) as st:
            wizard.run_wizard(lo, st, probe=not getattr(args, "no_probe", False))
    except Exception:  # noqa: BLE001 - the health gate must never stop the app launching
        console.warn("the health check could not complete; continuing in paper mode")
        if _debug_enabled():
            traceback.print_exc()


def cmd_auto(lo, args) -> int:
    # Unconditional, and before anything else touches the config.
    # `ensure_config_toml` is idempotent — it creates the file when absent and
    # repairs its encoding when present — but it used to run only on the install
    # branch below, so an install that *already existed* never reached it. That
    # is exactly backwards: the encoding repair (see textio.py) exists for
    # configs written by an older launcher, which by definition only occur on
    # installs that already exist. So the repair was unreachable for every user
    # who needed it, and the next thing to read the config — `config_is_live_ready`,
    # via `cmd_run` — crashed the launch on the unreadable file instead of
    # healing it.
    config.ensure_config_toml(lo)
    ready = state.probe(lo)
    if not ready.dashboard:
        _welcome()
        console.banner("Installing (one-time)…")
        opts = installer.InstallOptions(paper_only=getattr(args, "paper_only", False))
        if not installer.install(lo, opts):
            console.err("installation failed; see output above")
            console.info("Tip: run `l2arb doctor` to see which toolchain is missing.")
            return 1
    else:
        console.ok("already installed — launching")
    _health_gate(lo, args)
    return cmd_run(lo, args)


_COMMANDS = {"doctor", "health", "install", "setup", "run", "auto"}
_TOP_LEVEL = {"-h", "--help", "--version"}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Default to `auto` when invoked with no subcommand (e.g. a double-clicked
    # .exe) or when the first token is a run flag like `--live`.
    if argv and argv[0] in _TOP_LEVEL:
        pass
    elif not argv or argv[0] not in _COMMANDS:
        argv = ["auto", *argv]

    parser = build_parser()
    args = parser.parse_args(argv)
    lo = layout()
    # When frozen, unpack the bundled component sources into the install dir on
    # first run (no-op in a dev checkout / on later runs).
    payload.ensure_payload(lo)

    command = args.command or "auto"
    try:
        if command == "doctor":
            return cmd_doctor(lo)
        if command == "health":
            return cmd_health(lo, args)
        if command == "install":
            return cmd_install(lo, args)
        if command == "setup":
            return cmd_setup(lo, args)
        if command == "run":
            return cmd_run(lo, args)
        return cmd_auto(lo, args)
    except KeyboardInterrupt:
        console.warn("interrupted")
        return 130


def _run_main_safely(argv: list[str]) -> int:
    """Run ``main``, turning any crash into an exit code instead of letting it
    propagate. ``main`` only catches ``KeyboardInterrupt`` around its own command
    dispatch — argument parsing and the first-run payload unpack
    (``payload.ensure_payload``) run outside that, and any other exception type is
    never caught at all. Without this wrapper, an uncaught exception skips the
    "keep the window open" logic below entirely: a frozen exe's freshly-spawned
    console would vanish with the traceback never seen.
    """
    try:
        return main(argv)
    except KeyboardInterrupt:
        console.warn("interrupted")
        return 130
    except Exception:
        console.err("L2ArbBot hit an unexpected error:")
        traceback.print_exc()
        return 1


def _should_pause_before_exit(code: int) -> bool:
    """Double-clicking a frozen .exe spawns a console that Windows closes the
    instant the process exits, so on failure we pause for the user to read it.
    Gated on an interactive stdin — a non-interactive invocation (redirected or
    piped) has nobody there to press Enter, and ``input()`` would just hang it.
    """
    return is_frozen() and IS_WINDOWS and code != 0 and sys.stdin.isatty()


def _entrypoint() -> None:
    # When a frozen .exe is double-clicked with no args, default to auto and keep
    # the console open long enough to read any error.
    code = _run_main_safely(sys.argv[1:])
    if _should_pause_before_exit(code):
        try:
            input("\nPress Enter to close…")
        except EOFError:
            pass
    sys.exit(code)
