"""RUN mode: start the stack, gate on health, open the dashboard, supervise.

Two shapes:
  * paper (default, zero-config) — only the dashboard, simulated feed. Safe and
    instant: something to look at with no RPC endpoints.
  * live — engine (uvicorn) + ingestion (l2-ingest) + dashboard(external feed).
    Requires a filled config.toml; otherwise we fall back to paper and say so.

Execution always stays in paper mode — detection + simulation only. Broadcasting
a real flash-loan transaction is a separate, human-authorised action and is never
initiated here.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
import webbrowser

from . import config, console, state
from .health import HealthMonitor
from .paths import Layout
from .proc import Service


def wait_http(url: str, timeout: float = 30.0) -> bool:
    """Poll an HTTP endpoint until it answers 2xx or the timeout elapses."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:  # noqa: S310 (localhost only)
                if 200 <= resp.status < 300:
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.4)
    return False


def _service(lo: Layout, name: str, cmd, cwd, env, *, port: int) -> Service:
    return Service(
        name,
        cmd,
        cwd,
        env,
        lo.logs_dir / f"{name}.log",
        health_url=config.health_url(name, port),
    )


def run(lo: Layout, *, live: bool, port: int, open_browser: bool = True) -> int:
    ready = state.probe(lo)
    if not ready.dashboard:
        # Self-heal instead of erroring: a user who typed `run` before `install`
        # (or whose build was interrupted) just gets it built now — full when they
        # asked for live, dashboard-only otherwise.
        from . import installer

        console.warn("not built yet — installing now (one-time; this can take a few minutes)")
        installer.install(lo, installer.InstallOptions(paper_only=not live))
        ready = state.probe(lo)
        if not ready.dashboard:
            console.err("dashboard build failed — see the output above")
            return 1

    # Decide the effective mode.
    if live:
        if not ready.full:
            console.warn("live stack incomplete (engine/ingestion not built) — falling back to paper mode")
            live = False
        elif not config.config_is_live_ready(lo):
            console.warn(
                f"config.toml still has placeholder endpoints ({lo.config_toml}); "
                "fill real RPC endpoints + pools for live data — falling back to paper mode"
            )
            live = False

    lo.ensure_state_dirs()
    services: list[Service] = []

    if live:
        console.banner("Starting live stack (engine → ingestion → dashboard)")
        engine = _service(lo, "engine", config.engine_cmd(lo), lo.engine, {}, port=port)
        engine.start()
        services.append(engine)
        if not wait_http(f"http://127.0.0.1:{config.ENGINE_PORT}/health", 40):
            console.warn("engine did not become healthy in time; check .l2arb/logs/engine.log")
        ingestion = _service(lo, "ingestion", config.ingestion_cmd(lo), lo.ingestion, {}, port=port)
        ingestion.start()
        services.append(ingestion)
    else:
        console.banner("Starting dashboard (paper / simulation mode)")

    dash_env = config.dashboard_env(lo, live=live, port=port)
    dashboard = _service(lo, "dashboard", config.dashboard_cmd(lo), lo.dashboard / "backend", dash_env, port=port)
    dashboard.start()
    services.append(dashboard)

    url = f"http://localhost:{port}"
    if wait_http(f"{url}/api/health", 30):
        console.ok(f"dashboard is up at {url}")
    else:
        console.warn(f"dashboard health check timed out; try {url} manually (see .l2arb/logs/dashboard.log)")

    if open_browser:
        try:
            webbrowser.open(url)
        except OSError:
            pass

    # Hand off to the continuous health monitor: a live HUD that probes each
    # service, self-diagnoses faults, and self-heals by restarting a crashed or
    # wedged process (with backoff + a bounded budget). Recovery restarts infra
    # only — it never signs, submits, or re-broadcasts anything; execution stays
    # paper-by-default and human-gated.
    return HealthMonitor(services).run()
