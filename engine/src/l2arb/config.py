"""Typed application configuration.

All runtime tunables live here and are loaded from the environment (or a
``.env``) with the ``L2ARB__`` prefix and ``__`` as the nesting delimiter, e.g.::

    L2ARB__MIN_PROFIT_BPS=5
    L2ARB__CHAINS__ARBITRUM__HTTP=https://...
    L2ARB__CHAINS__ARBITRUM__WSS=wss://...
    L2ARB__BLOCKSCOUT__API_KEY=...

Configuration is read from a single **master ``.env`` at the repository root**
(shared by every component of the super-repo) and, if present, an **engine-local
``.env``** that overrides it; real environment variables override both. See the
repo-root ``.env.example`` for the full, documented surface.

There are **no secrets and no signing keys** in this configuration — only
read-only endpoints, an optional explorer rate-limit API key, and detection
tunables (see docs/SECURITY.md §1). Every external value is validated by pydantic
at load time so bad configuration fails loud and early rather than corrupting the
detection path.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _split_csv(raw: str) -> tuple[str, ...]:
    """Split a comma-separated endpoint string into a tuple, dropping blanks."""
    return tuple(part.strip() for part in raw.split(",") if part.strip())


# ── Blockscout verification oracle (data-integrity tier) ─────────────────────
# The engine cross-checks RPC-derived pool state against Blockscout — an
# independent explorer — as the bar for "verifiable on-chain data" (see
# docs/DATA_INTEGRITY.md §2). Each chain's Blockscout REST base is **public and
# fixed**, so an operator supplies only an API key; the endpoints are never asked
# for and never live in a ``.env``. This mapping is the single source of truth for
# them — keep it in step with the chains the engine reads.
BLOCKSCOUT_REST_BASES: dict[str, str] = {
    "arbitrum": "https://arbitrum.blockscout.com",
    "base": "https://base.blockscout.com",
    "optimism": "https://optimism.blockscout.com",
}


class BlockscoutConfig(BaseModel):
    """Config for the Blockscout verification oracle.

    Only :attr:`api_key` comes from the environment
    (``L2ARB__BLOCKSCOUT__API_KEY``); the per-chain REST endpoints are the fixed,
    public :data:`BLOCKSCOUT_REST_BASES`. An empty key is valid — Blockscout's
    public API works without one, just at a lower rate limit. The key is a
    rate-limit credential, not a write/signing secret: the engine only ever issues
    read requests to the explorer.
    """

    api_key: str = ""

    def rest_base(self, chain: str) -> str | None:
        """The Blockscout REST base URL for ``chain`` (case-insensitive), or
        ``None`` if the chain has no known Blockscout instance."""
        return BLOCKSCOUT_REST_BASES.get(chain.strip().lower())

    def verify_url(self, chain: str, path: str = "") -> str | None:
        """A ready-to-call Blockscout URL for ``chain``.

        Joins the fixed REST base with ``path`` and, when an API key is
        configured, appends it as the ``apikey`` query parameter (Blockscout's
        rate-limit credential). Returns ``None`` for a chain with no known
        Blockscout instance so callers fail loud rather than hitting a wrong host.
        """
        base = self.rest_base(chain)
        if base is None:
            return None
        url = f"{base.rstrip('/')}/{path.lstrip('/')}" if path else base
        if self.api_key:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}apikey={self.api_key}"
        return url

    @property
    def supported_chains(self) -> tuple[str, ...]:
        """Chains with a known, built-in Blockscout instance (sorted)."""
        return tuple(sorted(BLOCKSCOUT_REST_BASES))


class ChainEndpoints(BaseModel):
    """Read-only RPC endpoints for a single chain.

    ``http`` powers ``eth_call``/``eth_getLogs`` and cold-start batch loads;
    ``wss`` powers the low-latency streaming path (``eth_subscribe``). Each is a
    comma-separated list (a single endpoint is the common case; extra entries
    enable failover). Kept as plain strings so they load cleanly from a single
    environment variable; use :attr:`http_urls` / :attr:`wss_urls` to get them
    parsed into a tuple.
    """

    http: str = ""
    wss: str = ""

    @property
    def http_urls(self) -> tuple[str, ...]:
        """HTTP endpoints, parsed and normalised."""
        return _split_csv(self.http)

    @property
    def wss_urls(self) -> tuple[str, ...]:
        """WSS endpoints, parsed and normalised."""
        return _split_csv(self.wss)


def _discover_env_files() -> tuple[str, ...]:
    """Locate the ``.env`` files to load, ordered for pydantic precedence.

    Returns ``(master, engine_local)`` as strings. pydantic-settings lets the
    **last** file win, so the repo-root master is listed first and an engine-local
    ``.env`` (if present) overrides it; real environment variables still override
    both. Non-existent paths are ignored by pydantic.

    The engine always runs with its own directory as the working directory — the
    ``make``/pytest gates run there, and the launcher starts the process with
    ``cwd`` set to ``engine/`` — and it may be installed editable *or* copied into
    a venv. So we anchor discovery on the working directory (not ``__file__``,
    which lands in site-packages for a copied install) and walk up to the repo
    root: the first ancestor that holds the sibling component folders.
    """
    cwd = Path.cwd()
    engine_local = cwd / ".env"
    root: Path | None = None
    for candidate in (cwd, *cwd.parents):
        if (candidate / "engine").is_dir() and (candidate / "dashboard").is_dir():
            root = candidate
            break
    # Fall back to the parent dir when the marker isn't found (e.g. the engine
    # checked out standalone); a missing file is simply skipped.
    master = (root / ".env") if root is not None else (cwd.parent / ".env")
    return (str(master), str(engine_local))


class Settings(BaseSettings):
    """Root configuration object. Construct via :func:`get_settings`."""

    model_config = SettingsConfigDict(
        env_prefix="L2ARB__",
        env_nested_delimiter="__",
        env_file=_discover_env_files(),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Chains keyed by a short name ("arbitrum", "base", "optimism", ...).
    chains: dict[str, ChainEndpoints] = Field(default_factory=dict)

    # Independent verification oracle (Blockscout). Only an optional API key is
    # taken from the environment; the endpoints are built in. See
    # docs/DATA_INTEGRITY.md §2.
    blockscout: BlockscoutConfig = Field(default_factory=BlockscoutConfig)

    # Detection tunables (see docs/ARBITRAGE_THEORY.md §4 and docs/LATENCY.md).
    min_profit_bps: int = Field(default=5, ge=0)
    max_hops: int = Field(default=4, ge=2, le=8)
    max_latency_ms_p99: int = Field(default=250, gt=0)
    gas_safety_multiplier: float = Field(default=1.5, ge=1.0)

    # Infra (read side only).
    redis_url: str = "redis://localhost:6379/0"

    # Observability.
    log_level: str = "INFO"
    metrics_port: int = Field(default=9090, gt=0, lt=65536)

    @property
    def enabled_chains(self) -> list[str]:
        """Names of chains that have at least one HTTP endpoint configured."""
        return sorted(name for name, ep in self.chains.items() if ep.http_urls)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton (cached).

    Cached so configuration is parsed once. Tests that need a fresh instance
    should call ``get_settings.cache_clear()`` or construct :class:`Settings`
    directly with explicit values.
    """
    return Settings()
