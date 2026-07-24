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


def read(lo: Layout) -> dict:
    try:
        return json.loads(lo.state_file.read_text())
    except (OSError, ValueError):
        return {}


def write(lo: Layout, **fields) -> None:
    lo.ensure_state_dirs()
    data = read(lo)
    data.update(fields)
    data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    lo.state_file.write_text(json.dumps(data, indent=2))


def mark_component(lo: Layout, component: str, version: str = "") -> None:
    data = read(lo)
    built = data.get("built", {})
    built[component] = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "version": version,
    }
    write(lo, built=built)
