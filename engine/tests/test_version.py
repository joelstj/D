"""Smoke test: the package imports and exposes a version."""

from __future__ import annotations

import re

import pytest

import l2arb

pytestmark = pytest.mark.unit


def test_version_is_semver_like() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", l2arb.__version__)
