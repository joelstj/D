"""Build the runnable artifacts for each component into the workspace.

  engine     → a Python venv under .l2arb/venv-engine with l2arb + deps installed
  dashboard  → pnpm install + build (backend dist + frontend dist)
  ingestion  → cargo build --release → l2-ingest binary

Paper/simulation mode needs only the dashboard, so ``paper_only`` installs just
that — the fast path to a working dashboard with zero RPC configuration.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import console, proc, state
from .paths import Layout
from .prereqs import (
    detect_cargo,
    detect_pnpm,
    ensure_windows_prereqs,
    find_engine_python,
    which,
)


@dataclass
class InstallOptions:
    paper_only: bool = False  # dashboard only (no engine / ingestion)
    skip_engine: bool = False
    skip_ingestion: bool = False


def _pnpm_cmd() -> list[str] | None:
    if detect_pnpm().ok:
        return ["pnpm"]
    # Try to light up pnpm through Corepack (ships with Node >= 16).
    if which("corepack"):
        proc.run(["corepack", "enable", "pnpm"], prefix="corepack")
        if detect_pnpm().ok:
            return ["pnpm"]
    return None


def install_engine(lo: Layout) -> bool:
    py = find_engine_python()
    if not py:
        console.err("no Python 3.11/3.12 found for the engine venv; install one and retry")
        return False
    console.banner("Installing engine (Python detection core)")
    py_tokens = py.split()
    if not lo.venv_python().exists():
        if proc.run(py_tokens + ["-m", "venv", str(lo.venv_dir)], prefix="engine") != 0:
            return False
    vpy = str(lo.venv_python())
    proc.run([vpy, "-m", "pip", "install", "--upgrade", "pip", "wheel"], prefix="engine")
    # Install l2arb + its runtime deps straight from the component's pyproject.
    rc = proc.run([vpy, "-m", "pip", "install", str(lo.engine)], cwd=lo.engine, prefix="engine")
    if rc != 0:
        console.err("engine install failed (see output above)")
        return False
    state.mark_component(lo, "engine")
    console.ok("engine installed")
    return True


def install_dashboard(lo: Layout) -> bool:
    pnpm = _pnpm_cmd()
    if not pnpm:
        console.err("pnpm not available and could not be enabled via corepack; install Node >= 20")
        return False
    console.banner("Installing dashboard (API + React UI)")
    if proc.run(pnpm + ["install", "--prefer-offline"], cwd=lo.dashboard, prefix="dashboard") != 0:
        return False
    if proc.run(pnpm + ["build"], cwd=lo.dashboard, prefix="dashboard") != 0:
        return False
    state.mark_component(lo, "dashboard")
    console.ok("dashboard built")
    return True


def install_ingestion(lo: Layout) -> bool:
    if not detect_cargo().ok:
        console.warn("cargo (Rust >= 1.94) not found; skipping ingestion build")
        console.info("live on-chain data needs the l2-ingest binary — install Rust and re-run `install`")
        return False
    console.banner("Building ingestion (Rust l2-ingest)")
    rc = proc.run(
        ["cargo", "build", "--release", "--bin", "l2-ingest"],
        cwd=lo.ingestion,
        prefix="ingestion",
    )
    if rc != 0:
        console.err("ingestion build failed")
        return False
    state.mark_component(lo, "ingestion")
    console.ok("ingestion built")
    return True


def install(lo: Layout, opts: InstallOptions) -> bool:
    """Run the selected build steps. Returns True if the minimum (dashboard) is ready."""
    lo.ensure_state_dirs()
    ensure_windows_prereqs(need_rust=not opts.paper_only, need_engine=not opts.paper_only)

    dashboard_ok = install_dashboard(lo)

    engine_ok = ingestion_ok = False
    if not opts.paper_only:
        if not opts.skip_engine:
            engine_ok = install_engine(lo)
        if not opts.skip_ingestion:
            ingestion_ok = install_ingestion(lo)

    state.write(
        lo,
        last_install={
            "paper_only": opts.paper_only,
            "dashboard": dashboard_ok,
            "engine": engine_ok,
            "ingestion": ingestion_ok,
        },
    )

    console.banner("Install summary")
    console.info(f"dashboard : {'ready' if dashboard_ok else 'FAILED'}")
    if not opts.paper_only:
        console.info(f"engine    : {'ready' if engine_ok else 'skipped/failed'}")
        console.info(f"ingestion : {'ready' if ingestion_ok else 'skipped/failed'}")
    return dashboard_ok
