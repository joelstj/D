"""Unified configuration that wires the three services together.

The wiring itself is already the *default* of each component's contract, so this
module mostly (a) materialises a ``config.toml`` for the ingestion layer from its
shipped example and (b) computes the dashboard's environment so it consumes the
real detection feed. Ports:

  engine     127.0.0.1:8080   (uvicorn, POST /detect, GET /health)
  ingestion  0.0.0.0:9001     (ws output sink → dashboard)  +  :9100 metrics
  dashboard  127.0.0.1:<port> (REST + /ws + served UI; default 8787)

Live mode requires real RPC endpoints + pool registries in ``config.toml`` — we
never invent those (on-chain data integrity), so we detect the shipped
placeholders and refuse to pretend the live stack is ready.
"""

from __future__ import annotations

from .paths import Layout

ENGINE_PORT = 8080
INGEST_WS_PORT = 9001
# The ingestion observability router (GET /health + /metrics) is bound to
# `metrics_bind` in app/pipeline.rs, i.e. :9100 in config.example.toml — so the
# health probe targets 9100. (`health_bind` :9090 is now also served as a
# dedicated /health listener, but 9100 remains the probe target for both
# /health and /metrics.)
INGEST_METRICS_PORT = 9100
DASHBOARD_PORT = 8787

# Substrings that mark an unfilled example config (placeholders, not real state).
_PLACEHOLDER_MARKERS = ("YOUR_", "0xWETH", "0xUSDC", "0xWETH_USDC")


def ensure_config_toml(lo: Layout) -> str:
    """Materialise .l2arb/config.toml from the ingestion example if absent."""
    lo.ensure_state_dirs()
    dst = lo.config_toml
    if not dst.exists():
        example = lo.ingestion / "config" / "config.example.toml"
        if example.exists():
            dst.write_text(example.read_text())
    return str(dst)


def config_is_live_ready(lo: Layout) -> bool:
    """True only when config.toml has been filled with real endpoints/pools."""
    if not lo.config_toml.exists():
        return False
    text = lo.config_toml.read_text()
    return not any(marker in text for marker in _PLACEHOLDER_MARKERS)


def engine_cmd(lo: Layout) -> list[str]:
    return [
        str(lo.venv_python()),
        "-m",
        "uvicorn",
        "l2arb.api.http:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(ENGINE_PORT),
    ]


def ingestion_cmd(lo: Layout) -> list[str]:
    return [str(lo.ingest_binary), "--config", ensure_config_toml(lo)]


def dashboard_cmd(lo: Layout) -> list[str]:
    return ["node", str(lo.dashboard_backend_entry)]


def health_url(name: str, port: int) -> str | None:
    """The liveness endpoint the health monitor probes for a service.

    Returns ``None`` for a service with no HTTP health surface, in which case the
    monitor falls back to bare process liveness.
    """
    if name == "engine":
        return f"http://127.0.0.1:{ENGINE_PORT}/health"
    if name == "ingestion":
        return f"http://127.0.0.1:{INGEST_METRICS_PORT}/health"
    if name == "dashboard":
        return f"http://127.0.0.1:{port}/api/health"
    return None


def dashboard_env(lo: Layout, *, live: bool, port: int) -> dict[str, str]:
    env: dict[str, str] = {
        "PORT": str(port),
        # Execution stays in paper mode: the merged product detects and simulates;
        # broadcasting a live flash-loan tx is a separate, human-authorised step.
        "EXECUTION_MODE": "paper",
        "CORS_ORIGIN": f"http://localhost:{port}",
    }
    if lo.frontend_dist.exists():
        env["SERVE_STATIC_DIR"] = str(lo.frontend_dist)
    if live:
        env["DATA_SOURCE"] = "external"
        env["INGEST_FEED_URL"] = f"ws://127.0.0.1:{INGEST_WS_PORT}"
    else:
        env["DATA_SOURCE"] = "simulated"
    return env
