"""Unit tests for l2arb.logging — the L2ARB__LOG_LEVEL wiring."""

from __future__ import annotations

import logging

import pytest

from l2arb.config import Settings
from l2arb.logging import configure_logging

pytestmark = pytest.mark.unit


def test_configure_logging_sets_the_effective_root_level() -> None:
    resolved = configure_logging(Settings(log_level="DEBUG"))
    assert resolved == logging.DEBUG
    assert logging.getLogger().getEffectiveLevel() == logging.DEBUG


def test_configure_logging_reflects_a_stricter_level() -> None:
    resolved = configure_logging(Settings(log_level="WARNING"))
    assert resolved == logging.WARNING
    assert logging.getLogger().getEffectiveLevel() == logging.WARNING


def test_configure_logging_defaults_to_the_cached_settings_singleton(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from l2arb.config import get_settings

    monkeypatch.setenv("L2ARB__LOG_LEVEL", "ERROR")
    get_settings.cache_clear()
    try:
        resolved = configure_logging()
        assert resolved == logging.ERROR
    finally:
        get_settings.cache_clear()


def test_configure_logging_is_idempotent() -> None:
    first = configure_logging(Settings(log_level="INFO"))
    second = configure_logging(Settings(log_level="INFO"))
    assert first == second == logging.INFO
