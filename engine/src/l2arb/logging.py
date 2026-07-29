"""Structured logging setup — wires ``L2ARB__LOG_LEVEL`` into a real sink.

The engine has always accepted this setting (see :class:`l2arb.config.Settings`)
but never configured anything from it — a declared, tested, but dead knob. This
module is the missing wiring: call :func:`configure_logging` once at process
startup (the HTTP and stdin integration surfaces both do) to configure stdlib
``logging`` + ``structlog`` at the operator-chosen level.
"""

from __future__ import annotations

import logging

import structlog

from l2arb.config import Settings, get_settings

__all__ = ["configure_logging"]


def configure_logging(settings: Settings | None = None) -> int:
    """Configure stdlib ``logging`` + ``structlog`` from ``settings.log_level``.

    Idempotent — safe to call more than once (each app startup, each test);
    reconfiguring always converges to the same state. Returns the resolved
    numeric level (``logging.INFO`` etc.) so a caller can assert on it.
    """
    settings = settings or get_settings()
    level = logging.getLevelName(settings.log_level)  # validated upstream; see config.py
    if not isinstance(level, int):  # pragma: no cover - defensive; Settings already validates
        level = logging.INFO
    logging.basicConfig(level=level, format="%(message)s", force=True)
    logging.getLogger().setLevel(level)
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )
    return level
