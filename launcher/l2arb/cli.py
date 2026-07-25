"""Command-line entry point for the L2 Arbitrage Bot launcher.

Subcommands:
  doctor            report toolchains, install state, and config readiness
  install           build the components (``--paper-only`` for dashboard alone)
  setup             guided setup for live on-chain data (Arbitrum quick-start)
  run               start the stack (``--live`` for the full on-chain path)
  auto  (default)   install-if-needed, then run and open the dashboard

Running with **no subcommand** (e.g. double-clicking the .exe) performs ``auto``:
if the app is not installed it installs everything it can, then launches the
dashboard and opens the browser — exactly the "just run it" experience.
"""

from __future__ import annotations

import argparse
import sys

from . import __version__, console, config, installer, payload, prereqs, run, setup, state
from .paths import IS_WINDOWS, is_frozen, layout


def _add_run_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("--live", action="store_true", help="run the full on-chain stack (needs a filled config.toml)")
    p.add_argument("--paper", action="store_true", help="force paper/simulation mode")
    p.add_argument("--no-browser", action="store_true", help="do not open a browser window")
    p.add_argument("--port", type=int, default=config.DASHBOARD_PORT, help=f"dashboard port (default {config.DASHBOARD_PORT})")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="l2arb", description="L2 Arbitrage Flash-Loan Bot launcher")
    parser.add_argument("--version", action="version", version=f"l2arb-launcher {__version__}")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("doctor", help="report toolchains + install/config state")

    p_install = sub.add_parser("install", help="build the components")
    p_install.add_argument("--paper-only", action="store_true", help="build only the dashboard (no engine/ingestion)")
    p_install.add_argument("--skip-engine", action="store_true")
    p_install.add_argument("--skip-ingestion", action="store_true")

    p_setup = sub.add_parser("setup", help="guided setup for live on-chain data (Arbitrum quick-start)")
    p_setup.add_argument("--provider", help="RPC provider preset (alchemy | infura)")
    p_setup.add_argument("--key", help="your RPC provider API key (used with --provider)")
    p_setup.add_argument("--http", help="Arbitrum HTTPS RPC URL (skips the prompt)")
    p_setup.add_argument("--ws", help="Arbitrum WebSocket URL (default: derived from --http)")
    p_setup.add_argument("--backup", help="backup HTTPS URL appended for rate-limit failover")

    p_run = sub.add_parser("run", help="start the stack")
    _add_run_flags(p_run)

    p_auto = sub.add_parser("auto", help="install-if-needed then run (default)")
    _add_run_flags(p_auto)
    p_auto.add_argument("--paper-only", action="store_true", help="if installing, build only the dashboard")

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
    console.info(f"live-ready : {config.config_is_live_ready(lo)}")
    return 0


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
    return setup.run_setup(lo, args)


def _effective_live(lo, args) -> bool:
    if getattr(args, "paper", False):
        return False
    ready = state.probe(lo)
    if getattr(args, "live", False):
        return True  # run() re-validates and downgrades if it can't honour it
    # auto: go live only if everything is genuinely ready.
    return ready.full and config.config_is_live_ready(lo)


def cmd_run(lo, args) -> int:
    return run.run(lo, live=_effective_live(lo, args), port=args.port, open_browser=not args.no_browser)


def cmd_auto(lo, args) -> int:
    ready = state.probe(lo)
    if not ready.dashboard:
        console.banner("First run — installing the L2 Arbitrage Bot")
        opts = installer.InstallOptions(paper_only=getattr(args, "paper_only", False))
        config.ensure_config_toml(lo)
        if not installer.install(lo, opts):
            console.err("installation failed; see output above")
            return 1
    else:
        console.ok("already installed — launching")
    return cmd_run(lo, args)


_COMMANDS = {"doctor", "install", "setup", "run", "auto"}
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


def _entrypoint() -> None:
    # When a frozen .exe is double-clicked with no args, default to auto and keep
    # the console open long enough to read any error.
    code = main(sys.argv[1:])
    if is_frozen() and IS_WINDOWS and code != 0 and not sys.stdin.isatty():
        try:
            input("\nPress Enter to close…")
        except EOFError:
            pass
    sys.exit(code)
