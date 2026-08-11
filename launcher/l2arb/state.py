"""Install-state detection.

The *source of truth* is what is actually on disk (a built binary, a populated
venv, a compiled dashboard) — not a trusted flag — so a half-finished or
manually-deleted install is detected honestly. ``state.json`` only stores
metadata (timestamps, tool versions) for display.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

from . import textio
from .paths import Layout


@dataclass
class ComponentReadiness:
    engine: bool
    dashboard: bool
    ingestion: bool

    @property
    def dashboard_only(self) -> bool:
        """Enough to run the paper/simulation dashboard with no live stack."""
        return self.dashboard

    @property
    def full(self) -> bool:
        """Enough to run the full live stack (engine + ingestion + dashboard)."""
        return self.engine and self.dashboard and self.ingestion


def probe(lo: Layout) -> ComponentReadiness:
    engine = lo.venv_python().exists()
    dashboard = (
        (lo.dashboard / "node_modules").exists()
        and lo.dashboard_backend_entry.exists()
        and (lo.frontend_dist / "index.html").exists()
    )
    ingestion = lo.ingest_binary.exists()
    return ComponentReadiness(engine=engine, dashboard=dashboard, ingestion=ingestion)


def next_step(ready: ComponentReadiness, live_ready: bool) -> str:
    """The single most useful next action for the user, given what's on disk and
    whether the config is live-ready. Pure, so it's unit-tested and the wording is
    the same wherever it's shown (``doctor`` today, the wizard/HUD later)."""
    if not ready.dashboard:
        return "Run `l2arb` (or `l2arb install`) to build the app and open the dashboard."
    if not ready.full:
        return (
            "Paper mode is ready — run `l2arb run`. "
            "For real on-chain data, run `l2arb install` (builds the engine + Rust feed), then `l2arb setup`."
        )
    if not live_ready:
        return "Everything is built. Run `l2arb setup` to add your RPC endpoint, then `l2arb run --live`."
    return "You're live-ready — run `l2arb run --live`."


def read(lo: Layout) -> dict:
    try:
        return json.loads(textio.read_text(lo.state_file))
    except (OSError, ValueError):
        return {}


def write(lo: Layout, **fields) -> None:
    lo.ensure_state_dirs()
    data = read(lo)
    data.update(fields)
    data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    textio.write_text(lo.state_file, json.dumps(data, indent=2))


def mark_component(lo: Layout, component: str, version: str = "") -> None:
    data = read(lo)
    built = data.get("built", {})
    built[component] = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "version": version,
    }
    write(lo, built=built)
