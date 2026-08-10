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
* **All-chain setup** (``l2arb setup --all-chains``): walks every chain this
  component targets (arbitrum/base/optimism/unichain/ink). Each chain's RPC
  endpoint is auto-detected from the environment (common env-var spellings for
  an already-configured RPC credential) or prompted for individually; pools are
  auto-discovered on-chain (``ingestion/scripts/discover_pools.py``) or fall
  back to a shipped example, and any chain this can't fully verify is written
  disabled with the endpoint preserved and the exact next step spelled out —
  never a guessed-at address.

The endpoint fields accept the comma-separated ``primary, backup`` failover form
the ingestion layer supports. Everything here is pure string/dict logic (no I/O)
apart from the wizard drivers and the pool-discovery subprocess call, so the
logic is unit-tested deterministically.

Execution stays **paper-by-default and human-gated** — this only writes read-only
data-source config; it never enables broadcasting.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
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


# Every chain the ingestion layer supports (root CLAUDE.md §1) — kept in sync
# with `config.example.toml`'s `[[chains]]` blocks and `config/pools/*.example.toml`.
POOL_CHAINS: tuple[str, ...] = ("arbitrum", "base", "optimism", "unichain", "ink")


def materialize_pool_registries(lo: Layout, source_dir: Path | None = None) -> dict[str, Path]:
    """Copy every shipped, real per-chain pool registry into the state dir and
    return ``{chain: absolute path}``, so a generated config's `pool_registry`
    references resolve regardless of the ingestion binary's cwd — or whether
    the ingestion source tree is even writable (e.g. bundled inside the .exe).

    Previously only Arbitrum's registry was ever materialised anywhere
    automated (`materialize_arbitrum_pools`, used solely by the quick-start
    wizard); the other 4 chains' `pool_registry` paths pointed at files that
    were never created by any code path. Chains whose shipped example is
    missing are simply omitted from the result, not an error — a caller that
    requires one checks for it.
    """
    src_dir = source_dir or (lo.ingestion / "config" / "pools")
    dst_dir = lo.state_dir / "pools"
    dst_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, Path] = {}
    for chain in POOL_CHAINS:
        src = src_dir / f"{chain}.example.toml"
        if not src.exists():
            continue
        dst = dst_dir / f"{chain}.toml"
        shutil.copyfile(src, dst)
        out[chain] = dst
    return out


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


# ── Multi-chain guided setup (`l2arb setup --all-chains`) ────────────────────
# Generalizes the Arbitrum quick-start above to every chain this component
# targets: auto-detect an already-configured RPC endpoint per chain from the
# environment, prompt individually for whatever is still missing, and attempt
# real on-chain pool discovery per chain — falling back to a shipped example
# or an honest "curate this by hand" instruction, never a guess. Nothing here
# invents an address it can't source from the shipped, already-vetted
# constants below or discover on-chain via `discover_pools.py`.

KNOWN_CHAINS = ("arbitrum", "base", "optimism", "unichain", "ink")

# Structural per-chain metadata (chain_id / gas model / block time) for all 5
# target chains — copied from the already-shipped `config.example.toml`, not
# invented. Every chain gets this regardless of whether real token addresses
# are also known for it (see `_CHAIN_TEMPLATE` below).
_CHAIN_META: dict[str, dict] = {
    "arbitrum": {"chain_id": 42161, "block_time_ms": 250, "gas_model": "arbitrum", "gas_safety_multiplier": 1.6},
    "base": {"chain_id": 8453, "block_time_ms": 2000, "gas_model": "op_stack", "gas_safety_multiplier": 1.5},
    "optimism": {"chain_id": 10, "block_time_ms": 2000, "gas_model": "op_stack", "gas_safety_multiplier": 1.5},
    "unichain": {"chain_id": 130, "block_time_ms": 1000, "gas_model": "op_stack", "gas_safety_multiplier": 1.5},
    "ink": {"chain_id": 57073, "block_time_ms": 1000, "gas_model": "op_stack", "gas_safety_multiplier": 1.5},
}

# Real, already-vetted WETH/USDC addresses (matches contracts/config/addresses.js
# and the pool registries discover_pools.py has independently verified
# on-chain) for the chains this wizard can fully auto-assemble an *enabled*
# block for — every one of the 5 target chains. Unichain and Ink were added
# after independently re-verifying `docs/notes-arbitrage-gui-compile-deploy.md`'s
# sourced research live on-chain (each chain's own factory re-fingerprinted,
# every pool re-derived — see root CLAUDE.md §17's post-merge reconciliation
# with §16): Unichain's *majority* liquidity is Uniswap V4 (a different
# discovery mechanism — no per-pool factory), but it also has a real, usable
# V3 deployment at its own chain-specific factory address (not the
# cross-chain-default one the other three chains share); Ink genuinely has
# just the one real WETH/USDC pool today. A chain this wizard can't fully
# verify (a future gap, or live discovery failing for an otherwise-templated
# chain) still renders disabled with its endpoint preserved rather than a
# guessed-at enabled block — see `render_disabled_chain_block`.
_CHAIN_TEMPLATE: dict[str, dict] = {
    "base": {
        "weth": "0x4200000000000000000000000000000000000006",
        "usdc": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    },
    "optimism": {
        "weth": "0x4200000000000000000000000000000000000006",
        "usdc": "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85",
    },
    "unichain": {
        "weth": "0x4200000000000000000000000000000000000006",
        "usdc": "0x078D782b760474a361dDA0AF3839290b0EF57AD6",
    },
    "ink": {
        "weth": "0x4200000000000000000000000000000000000006",
        "usdc": "0x2D270e6886d130D724215A266106e6832161EAEd",
    },
}

# Environment variable name patterns checked, in order, for an already-
# configured endpoint per chain — so an operator who already has RPC creds in
# their shell/`.env` (common for this kind of bot) gets them picked up
# automatically instead of being asked to paste something already sitting
# right there. `{CHAIN}` is the chain name upper-cased.
_HTTP_ENV_PATTERNS = ("RPC_URL_{CHAIN}", "{CHAIN}_RPC_URL", "L2ARB__CHAINS__{CHAIN}__HTTP")
_WS_ENV_PATTERNS = ("L2ARB__CHAINS__{CHAIN}__WSS", "WS_URL_{CHAIN}", "{CHAIN}_WS_URL")


def _first_env_match(patterns: tuple[str, ...], chain: str, env: dict[str, str]) -> str | None:
    upper = chain.strip().upper()
    for pat in patterns:
        val = (env.get(pat.format(CHAIN=upper)) or "").strip()
        if val:
            return val
    return None


def detect_env_endpoints(chain: str, env: dict[str, str]) -> tuple[str | None, str | None]:
    """`(http_url, ws_url)` already present in `env` for `chain`, or `None` each
    if not found. Pure function — pass `os.environ` (or a fake dict) explicitly."""
    return _first_env_match(_HTTP_ENV_PATTERNS, chain, env), _first_env_match(_WS_ENV_PATTERNS, chain, env)


def resolve_chain_endpoints(chain: str, env: dict[str, str], prompt: Prompt) -> tuple[str, str] | None:
    """`(ws_url, http_url)` for `chain`: prefer an endpoint already sitting in
    `env`; otherwise prompt the operator for it individually, offering a skip
    (empty input). A `ws_url` missing from the environment is derived from the
    resolved `http_url` (same heuristic the Arbitrum quick-start already uses)
    rather than asked for separately — most providers don't need a second paste
    for it. Returns `None` when the operator skips this chain entirely."""
    http, ws = detect_env_endpoints(chain, env)
    if http:
        console.ok(f"{chain}: found an RPC endpoint already configured in your environment")
    else:
        console.info(f"Paste an HTTPS RPC URL for {chain} (Enter to skip this chain):")
        http = prompt(f"{chain} HTTPS RPC URL: ").strip()
        if not http:
            return None
    return (ws or derive_ws_url(http)), http


def _discover_pools_json(
    lo: Layout, chain: str, http_url: str, runner: Callable[..., tuple[int, str]]
) -> dict | None:
    """Invoke `discover_pools.py --json` for `chain` and return its parsed
    result, or `None` if the subprocess itself couldn't produce JSON at all
    (script missing, python not found, timeout) — distinct from the script
    running fine and reporting nothing found, which comes back as a normal
    result with an empty `pools` list."""
    script = lo.ingestion / "scripts" / "discover_pools.py"
    if not script.exists():
        return None
    _code, out = runner(
        [sys.executable, str(script), "--chain", chain, "--http-url", http_url, "--json"],
        cwd=lo.ingestion,
    )
    try:
        return json.loads(out)
    except (ValueError, TypeError):
        return None


def _pick_native_price_pool(result: dict | None, weth: str, usdc: str) -> str | None:
    """The best WETH/USDC pool address for native-price derivation from a
    `discover_pools.py` JSON result — prefers the 0.05% tier (matches the
    convention the Arbitrum quick-start already uses), else any pairing found."""
    if not result or not result.get("pools"):
        return None
    candidates = [
        p
        for p in result["pools"]
        if {p["token0"].lower(), p["token1"].lower()} == {weth.lower(), usdc.lower()}
    ]
    if not candidates:
        return None
    preferred = next((p for p in candidates if p["fee_pips"] == 500), None)
    return (preferred or candidates[0])["address"]


def materialize_chain_pools(
    lo: Layout, chain: str, http_url: str, runner: Callable[..., tuple[int, str]] = proc.capture
) -> tuple[Path | None, str, dict | None]:
    """Get `chain` a real pool registry, cheapest-and-most-current source
    first: live on-chain discovery, then the shipped `.example.toml` (if this
    chain has one), else neither — the caller falls back to an honest
    "curate this by hand" instruction rather than a guess. Returns
    `(materialized_path_or_None, a short human-readable note, the raw
    discovery result if one ran)`."""
    lo.ensure_state_dirs()
    dst = lo.state_dir / "pools" / f"{chain}.toml"
    dst.parent.mkdir(parents=True, exist_ok=True)

    result = _discover_pools_json(lo, chain, http_url, runner)
    if result and result.get("pools") and result.get("toml"):
        dst.write_text(result["toml"])
        return dst, f"discovered {len(result['pools'])} real pool(s) on-chain", result

    shipped = lo.ingestion / "config" / "pools" / f"{chain}.example.toml"
    if shipped.exists():
        shutil.copyfile(shipped, dst)
        return dst, "used the shipped example pool registry (discovery found nothing usable)", result

    return None, "no pool registry available yet", result


def render_chain_block(chain: str, ws_url: str, http_url: str, pool_registry: str, native_price_pool: str | None) -> str:
    """An *enabled* `[[chains]]` block for a chain in `_CHAIN_TEMPLATE`
    (WETH/USDC hubs, OP-stack gas model) — the same shape `config.example.toml`
    ships, with real resolved endpoints and pool registry substituted in."""
    meta = _CHAIN_META[chain]
    t = _CHAIN_TEMPLATE[chain]
    weth, usdc = t["weth"], t["usdc"]
    native_block = f"[chains.native_price_pools]\n{_quote(usdc)} = {_quote(native_price_pool)}\n" if native_price_pool else ""
    return f"""\
[[chains]]
name          = "{chain}"
chain_id      = {meta['chain_id']}
enabled       = true
ws_url        = {_toml_str(ws_url)}
http_url      = {_toml_str(http_url)}
block_time_ms = {meta['block_time_ms']}
gas_model     = "{meta['gas_model']}"
min_profit_bps        = 5.0
base_gas              = 150000
per_hop_gas           = 100000
gas_safety_multiplier = {meta['gas_safety_multiplier']}
reconcile_interval_ms = 2000
hubs        = [{_quote(weth)}, {_quote(usdc)}]
numeraires  = [{_quote(weth)}, {_quote(usdc)}]
weth        = {_quote(weth)}
pool_registry = {_toml_str(pool_registry)}
{native_block}
"""


def render_disabled_chain_block(chain: str, ws_url: str, http_url: str, pool_registry: str) -> str:
    """A *disabled* `[[chains]]` placeholder that still preserves a resolved
    endpoint when this wizard doesn't have enough verified data (tokens,
    pools) to bring the chain fully online — so the operator's input isn't
    silently thrown away, and the concrete next step is spelled out in the
    file itself rather than guessed at."""
    meta = _CHAIN_META[chain]
    return f"""\
[[chains]]
name          = "{chain}"
chain_id      = {meta['chain_id']}
# Disabled: no verified pool registry yet. Add real entries to the file at
# pool_registry below (see ingestion/config/pools/README.md for the schema, or
# try: python3 ingestion/scripts/discover_pools.py --chain {chain} \\
#   --http-url <this chain's RPC URL> --factory 0x... --token NAME=0x...
# ), then flip this to true.
enabled       = false
ws_url        = {_toml_str(ws_url)}
http_url      = {_toml_str(http_url)}
block_time_ms = {meta['block_time_ms']}
gas_model     = "{meta['gas_model']}"
min_profit_bps        = 5.0
base_gas              = 150000
per_hop_gas           = 100000
gas_safety_multiplier = {meta['gas_safety_multiplier']}
reconcile_interval_ms = 2000
hubs        = []
numeraires  = []
pool_registry = {_toml_str(pool_registry)}

"""


def multi_chain_config(chain_blocks: list[str]) -> str:
    """A complete `config.toml` from already-rendered `[[chains]]` blocks (via
    `render_chain_block`/`render_disabled_chain_block`, or the Arbitrum quick-
    start's own block extracted verbatim). Cross-chain detection stays off,
    same reasoning as the Arbitrum quick-start: it needs a verified bridge/
    asset address set this wizard doesn't have."""
    header = """\
# L2 Arbitrage Bot — multi-chain config (generated by `l2arb setup --all-chains`).
# Real endpoints came from you (or your environment); addresses are the same
# on-chain-verified constants config.example.toml ships, or freshly discovered
# and verified on-chain by scripts/discover_pools.py. The startup gate
# re-proves every pool again before it enters the live set. Execution stays
# paper/simulated.
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

"""
    return header + "".join(chain_blocks) + "[cross_chain]\nenabled = false\n"


def run_setup_all_chains(
    lo: Layout,
    prompt: Prompt = input,
    env: dict[str, str] | None = None,
    runner: Callable[..., tuple[int, str]] = proc.capture,
) -> int:
    """Guided setup across every chain this component targets. For each chain:
    auto-detect an already-configured RPC endpoint from the environment, or
    prompt individually for one (skippable); then attempt real on-chain pool
    discovery, falling back to a shipped example or an honest disabled
    placeholder that preserves the endpoint and spells out the next step.
    `runner` is a test seam for the pool-discovery subprocess call (see
    `materialize_chain_pools`) — production callers always omit it."""
    env = os.environ if env is None else env  # type: ignore[assignment]
    console.banner("Set up live on-chain data — all chains")
    console.info("Checking your environment for already-configured RPC endpoints,")
    console.info("then asking you individually for anything still missing.")
    console.info("Enter (empty) to skip a chain entirely.")

    blocks: list[str] = []
    enabled_chains: list[str] = []
    needs_pools: list[str] = []
    skipped: list[str] = []

    for chain in KNOWN_CHAINS:
        endpoints = resolve_chain_endpoints(chain, env, prompt)
        if endpoints is None:
            console.warn(f"{chain}: skipped (no endpoint)")
            skipped.append(chain)
            continue
        ws_url, http_url = endpoints

        pool_path, note, discovery = materialize_chain_pools(lo, chain, http_url, runner)
        console.info(f"{chain}: {note}")

        if pool_path is not None and chain == "arbitrum":
            full = arbitrum_quickstart_config(ws_url, http_url, str(pool_path))
            blocks.append(full[full.index("[[chains]]") : full.index("[cross_chain]")])
            enabled_chains.append(chain)
        elif pool_path is not None and chain in _CHAIN_TEMPLATE:
            t = _CHAIN_TEMPLATE[chain]
            native_pool = _pick_native_price_pool(discovery, t["weth"], t["usdc"])
            blocks.append(render_chain_block(chain, ws_url, http_url, str(pool_path), native_pool))
            enabled_chains.append(chain)
        else:
            target = lo.state_dir / "pools" / f"{chain}.toml"
            blocks.append(render_disabled_chain_block(chain, ws_url, http_url, str(target)))
            needs_pools.append(chain)

    if not blocks:
        console.warn("no chain got an endpoint — nothing written. Paper mode still works.")
        return 1

    cfg = multi_chain_config(blocks)
    lo.ensure_state_dirs()
    lo.config_toml.write_text(cfg)
    console.ok(f"wrote {lo.config_toml}")
    if enabled_chains:
        console.ok(f"live-ready now: {', '.join(enabled_chains)}")

    ok, note = validate_config(lo)
    (console.ok if ok else console.warn)(note)

    if skipped or needs_pools:
        console.banner("Needs your input before these chains go live")
        for chain in skipped:
            console.info(f"  {chain}: no endpoint given — re-run `l2arb setup --all-chains`, or hand-edit {lo.config_toml}")
        for chain in needs_pools:
            console.info(
                f"  {chain}: endpoint saved but disabled — add real pools to "
                f"{lo.state_dir / 'pools' / f'{chain}.toml'} "
                "(see ingestion/config/pools/README.md), then flip its `enabled` to true"
            )

    console.banner("Setup complete")
    console.info("Go live now with:  l2arb run --live")
    return 0 if ok else 1

