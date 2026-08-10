"""Guided setup: turn "hand-edit a TOML file" into a few prompts (or flags).

Going **live** (real on-chain data) is the one step that used to demand editing
``config.toml`` by hand — filling RPC endpoints, token addresses, and pool
registries across five chains. This module removes that barrier:

* **Arbitrum quick-start** (the hero path): the user pastes **one** RPC URL and we
  assemble a complete, valid, live-ready config for Arbitrum One using the *real,
  already-shipped* WETH/USDC token + pool addresses (the same ones in
  ``ingestion/config/pools/arbitrum.example.toml``, which the on-chain validation
  gate re-proves at startup — nothing is invented here).
* **Provider presets**: paste an Alchemy/Infura API key and we template each
  supported chain's endpoint URL; or paste full endpoint URLs directly.

The endpoint fields accept the comma-separated ``primary, backup`` failover form
the ingestion layer supports. Everything here is pure string/dict logic (no I/O)
apart from the wizard driver at the bottom, so it is unit-tested deterministically.

Execution stays **paper-by-default and human-gated** — this only writes read-only
data-source config; it never enables broadcasting.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

from . import console, proc
from .paths import Layout

# ── Real, on-chain-verified Arbitrum One addresses ───────────────────────────
# Sourced from ingestion/config/pools/arbitrum.example.toml. The startup gate
# re-proves every one on-chain before it enters the live set, so these are a
# concrete *starting point*, never blindly trusted state.
ARBITRUM_WETH = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"
ARBITRUM_USDC = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"  # native USDC
ARBITRUM_WETH_USDC_POOL = "0xC6962004f452bE9203591991D15f6b388e09E8D0"  # Uni V3 0.05%

# Well-known public endpoint URL shapes; the user supplies their own key. Only
# providers/chains we're confident about are listed — anything else uses the
# "paste the full URL" path, which never guesses.
PROVIDERS: dict[str, dict[str, str]] = {
    "alchemy": {
        "arbitrum": "https://arb-mainnet.g.alchemy.com/v2/{key}",
        "base": "https://base-mainnet.g.alchemy.com/v2/{key}",
        "optimism": "https://opt-mainnet.g.alchemy.com/v2/{key}",
    },
    "infura": {
        "arbitrum": "https://arbitrum-mainnet.infura.io/v3/{key}",
        "base": "https://base-mainnet.infura.io/v3/{key}",
        "optimism": "https://optimism-mainnet.infura.io/v3/{key}",
    },
}


def provider_http_url(provider: str, key: str, chain: str) -> str | None:
    """The HTTPS endpoint for `chain` on `provider` with the user's `key`, or None
    when the pairing isn't a known preset (fall back to pasting the full URL)."""
    tmpl = PROVIDERS.get(provider.strip().lower(), {}).get(chain.strip().lower())
    return tmpl.format(key=key.strip()) if tmpl and key.strip() else None


def derive_ws_url(http_url: str) -> str:
    """Best-effort WebSocket URL for an HTTPS endpoint of a known provider.

    Alchemy shares the path (``https``→``wss``); Infura moves it under ``/ws``.
    Anything else just swaps the scheme. Applied only to the *first* endpoint of a
    comma-separated list (the primary); backups keep their pasted form.
    """
    first = http_url.split(",")[0].strip()
    if not first:
        return ""
    if ".g.alchemy.com/" in first:
        return first.replace("https://", "wss://", 1)
    if ".infura.io/v3/" in first:
        return first.replace("https://", "wss://", 1).replace("/v3/", "/ws/v3/", 1)
    if first.startswith("https://"):
        return "wss://" + first[len("https://") :]
    if first.startswith("http://"):
        return "ws://" + first[len("http://") :]
    return first


def _quote(addr: str) -> str:
    return f'"{addr}"'


def _toml_str(value: str) -> str:
    """Render ``value`` as a TOML **basic** (double-quoted) string, escaping the
    characters TOML treats specially. Critically this escapes backslashes, so a
    Windows absolute path (``C:\\Users\\...\\arbitrum.toml``) is not read as a run
    of invalid TOML escape sequences (``\\U``, ``\\A``, ...) that make the entire
    generated ``config.toml`` unparseable — silently breaking the live path on the
    flagship ``.exe`` distribution.

    Every field built from *unvalidated user input* — not just the pool registry
    path — must go through this, not an f-string. ``ws_url``/``http_url`` are
    pasted by the operator (from a provider dashboard, a script, or a shell
    history) and can just as easily carry a stray ``"`` or ``\\`` (e.g. a
    copy-paste that grabbed surrounding quotes); left raw, that one character
    corrupts the whole generated ``config.toml`` in exactly the same way an
    unescaped Windows path used to."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def arbitrum_quickstart_config(ws_url: str, http_url: str, pool_registry: str) -> str:
    """A complete, valid, live-ready ingestion config for **Arbitrum One only**.

    Only `ws_url`/`http_url` (the user's RPC endpoints, comma-separated failover
    lists allowed) and `pool_registry` (path to the materialised Arbitrum pool
    file) vary; every address is the real, gate-re-proven constant above. Other
    chains and cross-chain scanning are disabled, so this is the smallest thing
    that produces genuine live data from a single endpoint.
    """
    weth, usdc, pool = ARBITRUM_WETH, ARBITRUM_USDC, ARBITRUM_WETH_USDC_POOL
    return f"""\
# L2 Arbitrage Bot — Arbitrum quick-start config (generated by `l2arb setup`).
# Real, on-chain-verified WETH/USDC addresses; the startup gate re-proves them.
# Only the RPC endpoints below came from you. Execution stays paper/simulated.
schema_version = 1

[engine]
transport                 = "http"
http_url                  = "http://127.0.0.1:8080"
subprocess_cmd            = "python -m l2arb.api.runner"
health_path               = "/health"
detect_path               = "/detect"
top_n                     = 10
max_hops                  = 4
call_timeout_ms           = 50
keep_alive                = true
first_request_incremental = false

[cadence]
mode            = "hybrid"
min_interval_ms = 25
max_interval_ms = 1000
incremental     = true

[output]
sink    = "ws"
ws_bind = "0.0.0.0:9001"

[observability]
health_bind  = "0.0.0.0:9090"
metrics_bind = "0.0.0.0:9100"
log_level    = "info"
log_format   = "json"

[infra]
multicall3          = "0xcA11bde05977b3631167028862bE2a173976CA11"
op_gas_price_oracle = "0x420000000000000000000000000000000000000F"
op_l1_block         = "0x4200000000000000000000000000000000000015"
arb_gas_info        = "0x000000000000000000000000000000000000006C"

[[chains]]
name          = "arbitrum"
chain_id      = 42161
enabled       = true
# Your RPC endpoints. Comma-separate a primary + backup(s) for auto-failover.
ws_url        = {_toml_str(ws_url)}
http_url      = {_toml_str(http_url)}
block_time_ms = 250
gas_model     = "arbitrum"
min_profit_bps        = 5.0
base_gas              = 150000
per_hop_gas           = 100000
gas_safety_multiplier = 1.6
reconcile_interval_ms = 2000
hubs        = [{_quote(weth)}, {_quote(usdc)}]
numeraires  = [{_quote(weth)}, {_quote(usdc)}]
weth        = {_quote(weth)}
pool_registry = {_toml_str(pool_registry)}
[chains.native_price_pools]
{_quote(usdc)} = {_quote(pool)}

[cross_chain]
enabled = false
"""


# ── Multi-chain "fill endpoints" preset (advanced) ───────────────────────────
# Maps a chain name to the placeholder endpoint strings the shipped example uses,
# so a user who wants all five chains can drop in their own URLs. (Token/pool
# placeholders still need curating — the quick-start path avoids that entirely.)
_ENDPOINT_PLACEHOLDERS: dict[str, tuple[str, ...]] = {
    "arbitrum": ("wss://YOUR_ARBITRUM_WS", "https://YOUR_ARBITRUM_ARCHIVE, https://YOUR_ARBITRUM_ARCHIVE_BACKUP"),
    "base": ("wss://YOUR_BASE_WS", "https://YOUR_BASE_ARCHIVE"),
    "optimism": ("wss://YOUR_OPTIMISM_WS", "https://YOUR_OPTIMISM_ARCHIVE"),
    "unichain": ("wss://YOUR_UNICHAIN_WS", "https://YOUR_UNICHAIN_ARCHIVE"),
    "ink": ("wss://YOUR_INK_WS", "https://YOUR_INK_ARCHIVE"),
}


def fill_chain_endpoints(example_text: str, endpoints: dict[str, tuple[str, str]]) -> str:
    """Replace a chain's placeholder ``ws_url``/``http_url`` in the shipped example
    with real values. `endpoints` maps chain name → ``(ws_url, http_url)``. Only the
    endpoint placeholders are touched; unmentioned chains are left as-is."""
    out = example_text
    for chain, (ws, http) in endpoints.items():
        ph = _ENDPOINT_PLACEHOLDERS.get(chain)
        if not ph:
            continue
        out = out.replace(f'"{ph[0]}"', f'"{ws}"', 1)
        out = out.replace(f'"{ph[1]}"', f'"{http}"', 1)
    return out


# ── Endpoint resolution (pure given a prompt fn) ─────────────────────────────

# The type of an interactive prompt: takes a message, returns the user's line.
Prompt = Callable[[str], str]


def _looks_like_url(s: str) -> bool:
    s = s.strip().lower()
    return s.startswith(("http://", "https://", "ws://", "wss://"))


def resolve_arbitrum_endpoints(args, prompt: Prompt) -> tuple[str, str] | None:
    """Work out the (ws_url, http_url) for the Arbitrum quick-start from CLI flags,
    falling back to interactive prompts. Returns None if the user gives nothing.

    Precedence: ``--provider/--key`` → ``--http`` → interactive. A ``--ws`` overrides
    the derived WebSocket URL; a ``--backup`` HTTPS endpoint is appended for failover.
    """
    http = ""
    provider = getattr(args, "provider", None)
    key = getattr(args, "key", None)
    if provider and key:
        http = provider_http_url(provider, key, "arbitrum") or ""
        if not http:
            console.warn(f"no preset URL for provider {provider!r}; paste a full URL instead")
    if not http:
        http = (getattr(args, "http", None) or "").strip()
    if not http:
        console.info("Get a free Arbitrum RPC endpoint from e.g. alchemy.com or infura.io,")
        console.info("then paste its HTTPS URL below (it may contain your key).")
        http = prompt("Arbitrum HTTPS RPC URL: ").strip()
    if not http:
        return None
    if not _looks_like_url(http):
        console.warn(f"that doesn't look like a URL: {http!r}")

    backup = (getattr(args, "backup", None) or "").strip()
    if not backup and getattr(args, "http", None) is None and not (provider and key):
        # Interactive only: offer a failover backup.
        backup = prompt("Optional backup HTTPS URL for rate-limit failover (Enter to skip): ").strip()
    if backup:
        http = f"{http}, {backup}"

    ws = (getattr(args, "ws", None) or "").strip() or derive_ws_url(http)
    return ws, http


# ── Materialisation + validation (file I/O) ──────────────────────────────────


def materialize_arbitrum_pools(lo: Layout, source: Path | None = None) -> Path | None:
    """Copy the shipped **real** Arbitrum pool registry into the state dir and return
    its path (absolute), so the generated config can point at it regardless of cwd.
    Returns None if the shipped example is missing."""
    src = source or (lo.ingestion / "config" / "pools" / "arbitrum.example.toml")
    if not src.exists():
        return None
    dst = lo.state_dir / "pools" / "arbitrum.toml"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    return dst


def write_arbitrum_quickstart(lo: Layout, ws_url: str, http_url: str) -> Path | None:
    """Materialise the Arbitrum pool registry and write a complete live-ready
    ``config.toml`` from the user's endpoints. Returns the config path, or None if
    the shipped pool example couldn't be found."""
    lo.ensure_state_dirs()
    pool_path = materialize_arbitrum_pools(lo)
    if pool_path is None:
        console.err("shipped Arbitrum pool registry not found; cannot build a live config")
        return None
    cfg = arbitrum_quickstart_config(ws_url, http_url, str(pool_path))
    lo.config_toml.write_text(cfg)
    return lo.config_toml


def validate_config(lo: Layout, runner: Callable[..., int] = proc.run) -> tuple[bool, str]:
    """Validate the generated config with ``l2-ingest --check-config`` when the binary
    is built. Returns ``(ok, note)``; a missing binary is *not* a failure — the config
    is still schema-shaped and will be validated on the next live run."""
    binary = lo.ingest_binary
    if not binary.exists():
        return True, "ingestion binary not built yet — config will be checked on live run"
    rc = runner(
        [str(binary), "--check-config", "--config", str(lo.config_toml)],
        cwd=lo.ingestion,
        prefix="setup",
    )
    return (rc == 0), ("config validated" if rc == 0 else "config validation failed (see output)")


# ── Wizard driver ────────────────────────────────────────────────────────────


def run_setup(lo: Layout, args, prompt: Prompt = input) -> int:
    """Guided (or flag-driven) setup for **live** on-chain data. Paper mode needs
    none of this; this only wires read-only RPC endpoints, never execution."""
    console.banner("Set up live on-chain data (Arbitrum quick-start)")
    console.info("Paper/simulation mode needs no setup — this is only for REAL data.")
    console.info("Everything but your RPC endpoint is pre-filled with real, on-chain-")
    console.info("verified addresses; the startup gate re-proves them. Execution stays paper.")

    endpoints = resolve_arbitrum_endpoints(args, prompt)
    if endpoints is None:
        console.warn("no RPC endpoint provided — nothing written. Paper mode still works.")
        return 1
    ws_url, http_url = endpoints

    cfg = write_arbitrum_quickstart(lo, ws_url, http_url)
    if cfg is None:
        return 1
    console.ok(f"wrote {cfg}")

    ok, note = validate_config(lo)
    (console.ok if ok else console.warn)(note)

    console.banner("Setup complete")
    console.info("Go live now with:  l2arb run --live")
    console.info("(or just relaunch — the app auto-detects a live-ready config)")
    return 0 if ok else 1

