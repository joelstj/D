"""Tests for the test-pairing guard (scripts/check_test_pairing.py)."""

from __future__ import annotations

from pathlib import Path

import pytest
from check_test_pairing import find_unpaired, main

pytestmark = pytest.mark.unit


def _make_pkg(root: Path, rel: str, body: str = "x = 1\n") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def test_flags_module_without_test(tmp_path: Path) -> None:
    src = tmp_path / "src" / "l2arb"
    tests = tmp_path / "tests"
    _make_pkg(src, "__init__.py", "")
    _make_pkg(src, "widget.py")
    tests.mkdir(parents=True, exist_ok=True)

    missing = find_unpaired(src, tests)
    assert missing == ["src/l2arb/widget.py"]


def test_satisfied_when_test_exists_anywhere(tmp_path: Path) -> None:
    src = tmp_path / "src" / "l2arb"
    tests = tmp_path / "tests"
    _make_pkg(src, "amm/widget.py")
    _make_pkg(tests, "amm/test_widget.py", "")
    assert find_unpaired(src, tests) == []


def test_init_files_are_exempt(tmp_path: Path) -> None:
    src = tmp_path / "src" / "l2arb"
    tests = tmp_path / "tests"
    _make_pkg(src, "__init__.py", "")
    _make_pkg(src, "sub/__init__.py", "")
    tests.mkdir(parents=True, exist_ok=True)
    assert find_unpaired(src, tests) == []


def test_missing_src_root_is_empty(tmp_path: Path) -> None:
    assert find_unpaired(tmp_path / "nope", tmp_path / "tests") == []


def test_repo_itself_is_fully_paired() -> None:
    """The real project must always satisfy its own pairing rule."""
    assert main() == 0
