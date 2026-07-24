"""Shared pytest fixtures and test isolation.

Keeps configuration tests deterministic by scrubbing any ``L2ARB__*`` variables
from the environment and clearing the settings cache around every test, so a
stray shell export can never make a test pass or fail spuriously.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from graphkit import GraphKit

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def gk() -> type[GraphKit]:
    """A namespace of factory helpers for building test graphs."""
    return GraphKit


@pytest.fixture
def repo_root() -> Path:
    """Absolute path to the repository root."""
    return REPO_ROOT


@pytest.fixture(autouse=True)
def _isolated_settings_env() -> Iterator[None]:
    """Remove L2ARB__* env vars and reset the settings cache for each test."""
    from l2arb.config import get_settings

    saved = {k: v for k, v in os.environ.items() if k.startswith("L2ARB__")}
    for k in saved:
        del os.environ[k]
    get_settings.cache_clear()
    try:
        yield
    finally:
        for k in list(os.environ):
            if k.startswith("L2ARB__"):
                del os.environ[k]
        os.environ.update(saved)
        get_settings.cache_clear()
