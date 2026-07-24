"""Typed exception hierarchy for the engine.

The single most important distinction here is **data errors vs infra errors**
(CLAUDE.md §5, ADR-003):

* :class:`DataError` means *the data is wrong* — a malformed price, a stale
  block, a failed verification. These are **bugs in the world's assumptions**
  and must fail **loud**; never retry them, never paper over them with a
  fallback value. Reporting a number the engine cannot stand behind is worse
  than reporting nothing.
* :class:`InfraError` means *the plumbing hiccuped* — a dropped RPC socket, a
  timed-out request, a rate limit. These are **expected** and should be
  retried / failed-over, not surfaced as detection results.

Adapters translate their library-specific exceptions into one of these two so
the core and application layers can have a single, principled policy: *raise on
:class:`DataError`, retry on :class:`InfraError`.*
"""

from __future__ import annotations

__all__ = [
    "ConfigError",
    "DataError",
    "InfraError",
    "IngestError",
    "L2ArbError",
    "PoolStateError",
    "RateLimitError",
    "RpcError",
    "StaleDataError",
    "SubscriptionError",
    "VerificationError",
]


class L2ArbError(Exception):
    """Base class for every error raised by :mod:`l2arb`."""


# --------------------------------------------------------------------------- #
# Data errors — the data is wrong. Fail loud; do NOT retry.
# --------------------------------------------------------------------------- #
class DataError(L2ArbError):
    """The data itself is invalid or untrustworthy. Non-retryable by design."""


class PoolStateError(DataError):
    """Decoded pool state is malformed or internally inconsistent.

    e.g. negative/zero reserves where liquidity is required, a V3 ``sqrtPrice``
    outside the valid range, or a reserve pair that fails the pool's invariant.
    """


class StaleDataError(DataError):
    """State is older than the per-chain freshness bound and cannot be trusted."""


class VerificationError(DataError):
    """Two independent on-chain sources disagreed, or provenance did not replay.

    Raised by the verification subsystem when a pool's decoded state cannot be
    corroborated by the independent oracle at the same block (docs/DATA_INTEGRITY).
    """


class IngestError(DataError):
    """Externally-supplied data failed validation at the ingestion boundary."""


# --------------------------------------------------------------------------- #
# Infra errors — the plumbing hiccuped. Retry / failover; do NOT surface.
# --------------------------------------------------------------------------- #
class InfraError(L2ArbError):
    """A transient infrastructure failure. Expected; retry or fail over."""


class RpcError(InfraError):
    """An RPC endpoint returned an error or an unusable response."""


class RateLimitError(InfraError):
    """An RPC/REST endpoint rate-limited us; back off and/or fail over."""


class SubscriptionError(InfraError):
    """A streaming subscription (WSS ``newHeads``/``logs``) dropped."""


# --------------------------------------------------------------------------- #
# Configuration — wrong before we even start. Fail loud at startup.
# --------------------------------------------------------------------------- #
class ConfigError(L2ArbError):
    """Configuration is missing or invalid; the engine cannot start safely."""
