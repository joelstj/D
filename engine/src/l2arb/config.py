"""Typed application configuration.

All runtime tunables live here and are loaded from the environment (or a local
``.env``) with the ``L2ARB__`` prefix and ``__`` as the nesting delimiter, e.g.::

    L2ARB__MIN_PROFIT_BPS=5
    L2ARB__CHAINS__ARBITRUM__HTTP=https://...
    L2ARB__CHAINS__ARBITRUM__WSS=wss://...

There are **no secrets and no signing keys** in this configuration — only
read-only endpoints and detection tunables (see docs/SECURITY.md §1). Every
external value is validated by pydantic at load time so bad configuration fails
loud and early rather than corrupting the detection path.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _split_csv(raw: str) -> tuple[str, ...]:
    """Split a comma-separated endpoint string into a tuple, dropping blanks."""
    return tuple(part.strip() for part in raw.split(",") if part.strip())


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


class Settings(BaseSettings):
    """Root configuration object. Construct via :func:`get_settings`."""

    model_config = SettingsConfigDict(
        env_prefix="L2ARB__",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Chains keyed by a short name ("arbitrum", "base", "optimism", ...).
    chains: dict[str, ChainEndpoints] = Field(default_factory=dict)

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
