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

import re

from . import console, setup, textio
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

# General net alongside the literal list above. `_PLACEHOLDER_MARKERS` is a fixed
# snapshot of the placeholder spellings `config.example.toml` happened to use when
# it was written; it silently misses any placeholder shape added later that
# doesn't start with "YOUR_"/"0xWETH"/"0xUSDC" — e.g. Unichain's V4 infra fields
# (`uniswap_v4_pool_manager = "0xUNICHAIN_V4_POOLMANAGER"`, `..._state_view =
# "0xUNICHAIN_V4_STATEVIEW"`). A real on-chain address is always `0x` followed by
# 40 hex digits and nothing else, so any `0x`-prefixed token containing a
# non-hex-digit character (a letter outside a-f/A-F, or an underscore) cannot be
# a real address — it can only be an unfilled placeholder. Without this, a user
# who filled every named marker but missed a newer placeholder shape would be
# told the config is live-ready and `run --live` would launch the live stack
# against a fake address instead of falling back to paper mode — the same
# "looks healthy, isn't" shape prior audits (§9/§11/§12) flagged elsewhere in
# this repo, just for this config-readiness gate.
_HEX_0X_TOKEN_RE = re.compile(r"0x[0-9A-Za-z_]+")
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")


def _has_non_hex_0x_token(text: str) -> bool:
    return any(
        any(ch not in _HEX_DIGITS for ch in match.group()[2:]) for match in _HEX_0X_TOKEN_RE.finditer(text)
    )


def ensure_config_toml(lo: Layout) -> str:
    """Materialise .l2arb/config.toml from the ingestion example if absent, with
    every chain's real shipped pool registry copied alongside it into the
    writable state dir and referenced by absolute path.

    Previously this was a verbatim copy, so every chain's `pool_registry`
    pointed at `config/pools/<chain>.toml` — a relative path that resolved (if
    at all) only against the ingestion source tree's own directory, which may
    not be writable (e.g. bundled inside the .exe) and was never populated by
    any automated path except Arbitrum's quick-start wizard. Rewriting each
    reference to the materialised absolute path makes every chain's pool data
    actually reachable, the same way the quick-start path already worked for
    Arbitrum alone.
    """
    lo.ensure_state_dirs()
    dst = lo.config_toml
    if not dst.exists():
        example = lo.ingestion / "config" / "config.example.toml"
        if example.exists():
            text = textio.read_text(example)
            for chain, pool_path in setup.materialize_pool_registries(lo).items():
                placeholder = f'pool_registry = "config/pools/{chain}.toml"'
                text = text.replace(placeholder, f"pool_registry = {setup._toml_str(str(pool_path))}", 1)
            textio.write_text(dst, text)
    elif textio.repair_encoding(dst):
        # An install predating the UTF-8 fix (see textio.py) left a config the
        # Rust ingestion binary cannot read at all, so `l2-ingest` died on every
        # start and the health HUD showed it permanently "failed". Heal it here
        # rather than making the user find and delete a file under AppData.
        console.warn(f"repaired the text encoding of {dst} (it was not valid UTF-8; original kept as .bak)")
    return str(dst)


def config_is_live_ready(lo: Layout) -> bool:
    """True only when config.toml has been filled with real endpoints/pools."""
    if not lo.config_toml.exists():
        return False
    text = textio.read_text(lo.config_toml)
    if any(marker in text for marker in _PLACEHOLDER_MARKERS):
        return False
    return not _has_non_hex_0x_token(text)


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
