"""The catalog of everything the app needs from you, and how to get each one.

This is the single source of truth behind both the health check
(:mod:`l2arb.healthcheck`) and the guided setup (:mod:`l2arb.wizard`): one
:class:`Requirement` per value the stack needs — every RPC endpoint, WebSocket
URL, API key, wallet address, and tuning override — carrying not just a
validator but the full user-facing explanation of *what it is*, *why the app
needs it*, *where to get it*, and *what a correct answer looks like*.

Keeping the guidance next to the validator is deliberate: the wizard never has
to guess how to describe a field, and a new requirement cannot be added without
also explaining itself.

Three tiers decide what "100% healthy" means:

``BLOCKING``     the app cannot do the job you asked for without it. These, and
                only these, are what the health percentage is computed over — so
                reaching 100% is a real statement, not a participation trophy.
``RECOMMENDED`` the app runs without it but is measurably worse (harsher rate
                limits, no profit destination recorded). Reported, prompted for,
                never blocking.
``OPTIONAL``    tuning knobs with safe built-in defaults.

**On addresses and pools.** Token, DEX, and pool addresses are deliberately
*not* prompt-able. They are not credentials — they are on-chain facts, already
shipped verified in ``ingestion/config/pools/*.example.toml`` and re-proven
on-chain by the ingestion layer's startup gate, or discovered live by
``ingestion/scripts/discover_pools.py``. Asking a user to hand-type a pool
address would invite exactly the fabricated-market-data failure root
``CLAUDE.md`` §2 invariant 1 forbids. The health check therefore *verifies* that
each selected chain has a real, non-empty pool registry and repairs it
automatically, rather than making it something to type.

**On private keys.** None is collected, and none is needed to reach 100%. The
detection stack holds no keys and signs nothing; profit is paid to a wallet
address you supply (a public value), and the only signing in the product is done
by your own MetaMask. See :mod:`l2arb.credentials` for the full reasoning.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

# ── Tiers ────────────────────────────────────────────────────────────────────
BLOCKING = "blocking"
RECOMMENDED = "recommended"
OPTIONAL = "optional"

# ── Categories (used for grouping in the report/wizard) ──────────────────────
CAT_RPC = "rpc"
CAT_WS = "websocket"
CAT_API_KEY = "api_key"
CAT_WALLET = "wallet"
CAT_TUNING = "tuning"
CAT_DASHBOARD = "dashboard"


# ── Per-chain facts ──────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ChainInfo:
    """Identity of one supported L2, for building that chain's requirements."""

    key: str
    display: str
    chain_id: int
    #: The chain's own official public endpoint. Sourced from this repo's
    #: ``contracts/hardhat.config.js``, which already uses each as its default
    #: network URL — not from memory. Usable to get started, but heavily rate
    #: limited and not archive-capable, so the wizard offers it as a fallback
    #: and says plainly what the trade-off is.
    public_http: str
    #: Whether a mainstream provider (Alchemy/Infura) has a preset in
    #: :mod:`l2arb.setup`. Chains without one need a pasted URL.
    has_provider_preset: bool


CHAINS: dict[str, ChainInfo] = {
    "arbitrum": ChainInfo("arbitrum", "Arbitrum One", 42161, "https://arb1.arbitrum.io/rpc", True),
    "base": ChainInfo("base", "Base", 8453, "https://mainnet.base.org", True),
    "optimism": ChainInfo("optimism", "OP Mainnet", 10, "https://mainnet.optimism.io", True),
    "unichain": ChainInfo("unichain", "Unichain", 130, "https://mainnet.unichain.org", False),
    "ink": ChainInfo("ink", "Ink", 57073, "https://rpc-gel.inkonchain.com", False),
}

#: Order the wizard walks chains in — Arbitrum first because it is the
#: quick-start hero path and has the deepest shipped pool registry.
CHAIN_ORDER: tuple[str, ...] = ("arbitrum", "base", "optimism", "unichain", "ink")


# ── Validators ───────────────────────────────────────────────────────────────
# Each returns None when the value is acceptable, or a short, specific error
# message the wizard shows verbatim before re-prompting.

Validator = Callable[[str], "str | None"]

_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def _each_endpoint(value: str) -> list[str]:
    """Split the comma-separated ``primary, backup…`` failover form the ingestion
    layer accepts, dropping empties."""
    return [part.strip() for part in value.split(",") if part.strip()]


def validate_https_url(value: str) -> str | None:
    parts = _each_endpoint(value)
    if not parts:
        return "empty — paste the full URL, starting with https://"
    for part in parts:
        if not part.lower().startswith(("http://", "https://")):
            return f"{part!r} must start with https:// (or http:// for a local node)"
        if " " in part:
            return f"{part!r} contains a space — a URL cannot"
    return None


def validate_ws_url(value: str) -> str | None:
    parts = _each_endpoint(value)
    if not parts:
        return "empty — paste the full URL, starting with wss://"
    for part in parts:
        if not part.lower().startswith(("ws://", "wss://")):
            return f"{part!r} must start with wss:// (or ws:// for a local node)"
        if " " in part:
            return f"{part!r} contains a space — a URL cannot"
    return None


def validate_address(value: str) -> str | None:
    v = value.strip()
    if not _ADDRESS_RE.match(v):
        return "must be an Ethereum address: 0x followed by exactly 40 hex characters"
    return None


def validate_api_key(value: str) -> str | None:
    v = value.strip()
    if not v:
        return "empty"
    if any(ch.isspace() for ch in v):
        return "contains whitespace — an API key never does; check for a stray copy-paste newline"
    return None


def validate_port(value: str) -> str | None:
    try:
        port = int(value.strip())
    except ValueError:
        return "must be a whole number"
    if not (1 <= port <= 65535):
        return "must be between 1 and 65535"
    if port < 1024:
        return "ports below 1024 need administrator rights on most systems — pick a higher one"
    return None


def _number_validator(lo: float, hi: float, *, integer: bool = False) -> Validator:
    def _validate(value: str) -> str | None:
        try:
            num = int(value.strip()) if integer else float(value.strip())
        except ValueError:
            return "must be a whole number" if integer else "must be a number"
        if not (lo <= num <= hi):
            return f"must be between {lo} and {hi}"
        return None

    return _validate


# ── The requirement record ───────────────────────────────────────────────────
@dataclass(frozen=True)
class Requirement:
    """One value the app needs, with everything needed to ask for it well."""

    key: str
    title: str
    category: str
    tier: str
    what: str
    why: str
    where: tuple[str, ...]
    looks_like: str
    validator: Validator
    env_var: str | None = None
    is_secret: bool = False
    default: str | None = None
    chain: str | None = None
    #: A value the app can work out for itself (e.g. a WebSocket URL derived
    #: from an already-answered HTTPS one). The wizard offers it as the default
    #: answer so the user usually just presses Enter.
    derived_from: str | None = None
    #: Extra options the wizard offers as numbered picks instead of free text.
    suggestions: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    def validate(self, value: str) -> str | None:
        return self.validator(value)


# ── Catalog construction ─────────────────────────────────────────────────────

_PROVIDER_STEPS: tuple[str, ...] = (
    "The quickest route is a free account with an RPC provider. Any of these work:",
    "  • Alchemy   — https://dashboard.alchemy.com  → Apps → Create new app → pick the",
    "                network → the app's 'API key'/'Endpoints' tab shows the HTTPS URL.",
    "  • Infura    — https://app.infura.io          → Create new API key → Active Endpoints",
    "                → tick the network → copy the HTTPS URL.",
    "  • QuickNode — https://dashboard.quicknode.com → Create endpoint → copy 'HTTP Provider'.",
    "  • dRPC/Ankr — https://drpc.org or https://www.ankr.com/rpc — usable with no signup.",
    "The free tiers are enough for this bot. You want an endpoint that supports",
    "eth_getLogs and archive reads; every provider above does on its default plan.",
)


def _chain_http_requirement(info: ChainInfo) -> Requirement:
    return Requirement(
        key=f"rpc.{info.key}.http",
        title=f"{info.display} — HTTPS RPC endpoint",
        category=CAT_RPC,
        tier=BLOCKING,
        chain=info.key,
        env_var=f"L2ARB__CHAINS__{info.key.upper()}__HTTP",
        is_secret=True,
        what=(
            f"The HTTPS URL the bot calls to read {info.display}'s on-chain state — pool "
            "reserves, slot0 prices, gas, and block headers. It usually has your personal "
            "API key embedded in the path, which is why it is treated as a secret and "
            "masked whenever it is shown back to you."
        ),
        why=(
            f"Without it the ingestion layer cannot read a single {info.display} pool, so no "
            "opportunity on this chain can ever be detected. This is the one value the app "
            "genuinely cannot work out or infer for you."
        ),
        where=_PROVIDER_STEPS
        + (
            "",
            f"No account at all? {info.display}'s own free public endpoint is offered as a",
            "numbered option below. It works, but it is aggressively rate limited and not",
            "archive-grade, so detection will be slower and patchier than with a real key.",
            "",
            "Tip: you can paste a COMMA-SEPARATED list — 'https://primary, https://backup' —",
            "and the bot fails over automatically when one endpoint rate-limits you.",
        ),
        looks_like=(
            "https://arb-mainnet.g.alchemy.com/v2/AbC123…    (Alchemy)\n"
            "https://arbitrum-mainnet.infura.io/v3/AbC123…   (Infura)"
        ),
        validator=validate_https_url,
        suggestions=((info.public_http, f"{info.display}'s free public endpoint (rate limited)"),),
    )


def _chain_ws_requirement(info: ChainInfo) -> Requirement:
    return Requirement(
        key=f"rpc.{info.key}.ws",
        title=f"{info.display} — WebSocket (WSS) endpoint",
        category=CAT_WS,
        tier=BLOCKING,
        chain=info.key,
        env_var=f"L2ARB__CHAINS__{info.key.upper()}__WSS",
        is_secret=True,
        derived_from=f"rpc.{info.key}.http",
        what=(
            "The streaming counterpart of the HTTPS endpoint above. The bot subscribes to it "
            "to be pushed every new block header the instant it lands, instead of polling."
        ),
        why=(
            "Arbitrage windows close in well under a second. Without a WebSocket the mirrored "
            "pool state is never refreshed by live block events, so the bot keeps reporting "
            "prices that have already moved — the data goes stale while still looking healthy. "
            "This is why it counts as blocking rather than merely nice to have."
        ),
        where=(
            "It is the same endpoint as your HTTPS URL, on a different scheme — so the app",
            "derives it for you and you can normally just press Enter to accept it.",
            "  • Alchemy: identical URL with https:// swapped for wss://",
            "  • Infura:  https://…/v3/KEY becomes wss://…/ws/v3/KEY",
            "If your provider lists a separate 'WSS'/'WebSocket' URL in its dashboard, paste",
            "that instead — it is always authoritative over the derived guess.",
        ),
        looks_like=(
            "wss://arb-mainnet.g.alchemy.com/v2/AbC123…\n"
            "wss://arbitrum-mainnet.infura.io/ws/v3/AbC123…"
        ),
        validator=validate_ws_url,
    )


def chain_requirements(chain: str) -> list[Requirement]:
    """The blocking requirements for one selected chain."""
    info = CHAINS[chain]
    return [_chain_http_requirement(info), _chain_ws_requirement(info)]


BLOCKSCOUT_API_KEY = Requirement(
    key="engine.blockscout_api_key",
    title="Blockscout API key (independent price verification)",
    category=CAT_API_KEY,
    tier=RECOMMENDED,
    env_var="L2ARB__BLOCKSCOUT__API_KEY",
    is_secret=True,
    what=(
        "A read-only rate-limit credential for Blockscout, the block explorer the engine "
        "cross-checks its RPC-derived pool state against. It grants no write access and is "
        "not a signing key."
    ),
    why=(
        "The engine only marks an opportunity 'verified' when a second, independent source "
        "agrees with what the RPC told it. Blockscout's API works without a key but is "
        "rate limited, so under load some pools fall back to unverified and are skipped. "
        "A free key keeps the verification tier running at full rate."
    ),
    where=(
        "1. Go to https://blockscout.com and sign in (free).",
        "2. Open Account → API keys → Add API key.",
        "3. Copy the generated key and paste it here.",
        "",
        "You can safely skip this — the app stays fully functional, just with a lower",
        "verification throughput. Nothing else about it needs configuring: the per-chain",
        "explorer endpoints are already built into the engine.",
    ),
    looks_like="a 32-character hex-ish string, e.g. 9f2c1b7e4a8d…",
    validator=validate_api_key,
)


PROFIT_RECEIVER = Requirement(
    key="wallet.profit_receiver",
    title="Your wallet address (where profit is paid)",
    category=CAT_WALLET,
    tier=RECOMMENDED,
    env_var="PROFIT_RECEIVER",
    what=(
        "The PUBLIC Ethereum address of the wallet you want arbitrage profit sent to — the "
        "'0x…' string MetaMask shows at the top of the account screen. This is an address, "
        "not a key: it can only receive, never spend."
    ),
    why=(
        "When a flash-loan arbitrage settles, the contract forwards 100% of the profit to "
        "this address in the same atomic transaction. Recording it here means the deploy and "
        "execution screens are pre-filled with the right destination instead of you retyping "
        "it. Left unset, profit defaults to whichever wallet signs the transaction."
    ),
    where=(
        "1. Open MetaMask.",
        "2. Click the account name at the top — this copies the address to your clipboard.",
        "3. Paste it here.",
        "",
        "NEVER paste a private key or a seed phrase — not here, not anywhere in this app.",
        "This bot holds no keys and signs nothing; every real transaction is signed by you",
        "in MetaMask. Any tool that asks you to type a seed phrase is stealing your funds.",
    ),
    looks_like="0x50A71dF7DfC5850e8434C7c8A564366F4980183b",
    validator=validate_address,
)


DASHBOARD_PORT = Requirement(
    key="dashboard.port",
    title="Dashboard port",
    category=CAT_DASHBOARD,
    tier=OPTIONAL,
    env_var="PORT",
    default="8787",
    what="The local port the dashboard's web UI and API are served on.",
    why=(
        "Only worth changing if something else on your machine already uses 8787 — the "
        "symptom is the dashboard failing to start with an 'address in use' error."
    ),
    where=("Pick any free port above 1024. The app opens http://localhost:<port> for you.",),
    looks_like="8787",
    validator=validate_port,
)


MIN_PROFIT_BPS = Requirement(
    key="engine.min_profit_bps",
    title="Minimum profit threshold (basis points)",
    category=CAT_TUNING,
    tier=OPTIONAL,
    env_var="L2ARB__MIN_PROFIT_BPS",
    default="5",
    what=(
        "The smallest net edge, in basis points (1 bp = 0.01%), an opportunity must clear "
        "AFTER modelled gas and fees before the bot will report it."
    ),
    why=(
        "Lower surfaces more candidates but more of them are noise that evaporates before "
        "you could act. Higher reports fewer, higher-conviction opportunities. The shipped "
        "default of 5 bp (0.05%) is a sane starting point for L2 gas costs."
    ),
    where=("Nothing to fetch — just a number. Leave the default unless you know you want it different.",),
    looks_like="5     (0.05%)      ·   25  (0.25%, conservative)",
    validator=_number_validator(0, 10_000),
)


MAX_HOPS = Requirement(
    key="engine.max_hops",
    title="Maximum route length (hops)",
    category=CAT_TUNING,
    tier=OPTIONAL,
    env_var="L2ARB__MAX_HOPS",
    default="4",
    what="How many swaps a single arbitrage route may chain together.",
    why=(
        "More hops can find more exotic cycles but cost more gas and take longer to search. "
        "The engine supports 2 to 8; 4 is the shipped default."
    ),
    where=("Nothing to fetch — just a number between 2 and 8.",),
    looks_like="4",
    validator=_number_validator(2, 8, integer=True),
)


MAX_POOL_AGE = Requirement(
    key="engine.max_pool_age_seconds",
    title="Pool freshness limit (seconds)",
    category=CAT_TUNING,
    tier=OPTIONAL,
    env_var="L2ARB__MAX_POOL_AGE_SECONDS",
    default="120",
    what="Reject any route touching pool state older than this many seconds.",
    why=(
        "A data-integrity gate: stale prices produce opportunities that never really existed. "
        "Lowering it is stricter and safer; raising it past a couple of minutes risks acting "
        "on prices that have already moved."
    ),
    where=("Nothing to fetch — just a number of seconds.",),
    looks_like="120",
    validator=_number_validator(1, 3600, integer=True),
)


#: Requirements that are not tied to any one chain.
GLOBAL_REQUIREMENTS: tuple[Requirement, ...] = (
    BLOCKSCOUT_API_KEY,
    PROFIT_RECEIVER,
    DASHBOARD_PORT,
    MIN_PROFIT_BPS,
    MAX_HOPS,
    MAX_POOL_AGE,
)


def catalog(chains: list[str] | tuple[str, ...]) -> list[Requirement]:
    """Every requirement for an install that runs exactly ``chains``.

    Chains are walked in :data:`CHAIN_ORDER` (not the caller's order) so the
    report and the wizard always present them the same way; unknown names are
    ignored rather than raising, since the selection is user-supplied.
    """
    selected = [c for c in CHAIN_ORDER if c in set(chains)]
    out: list[Requirement] = []
    for chain in selected:
        out.extend(chain_requirements(chain))
    out.extend(GLOBAL_REQUIREMENTS)
    return out


def by_key(chains: list[str] | tuple[str, ...]) -> dict[str, Requirement]:
    return {r.key: r for r in catalog(chains)}


#: Key of the meta-setting recording which chains the operator chose to run.
SELECTED_CHAINS_KEY = "chains.selected"
