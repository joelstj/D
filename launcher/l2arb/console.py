"""Minimal, dependency-free console output with optional ANSI colour.

Colour is enabled only for interactive TTYs and disabled when ``NO_COLOR`` is
set or output is redirected, so logs stay clean in files and CI.
"""

from __future__ import annotations

import os
import sys

_USE_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None and os.name != "nt"


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOR else text


def banner(text: str) -> None:
    line = "─" * max(8, min(72, len(text) + 4))
    print(_c("36", f"\n{line}\n  {text}\n{line}"))


def step(text: str) -> None:
    print(_c("36", f"▶ {text}"))


def ok(text: str) -> None:
    print(_c("32", f"✓ {text}"))


def warn(text: str) -> None:
    print(_c("33", f"! {text}"))


def err(text: str) -> None:
    print(_c("31", f"✗ {text}"), file=sys.stderr)


def info(text: str) -> None:
    print(f"  {text}")
