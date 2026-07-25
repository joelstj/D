"""Minimal, dependency-free console output with optional ANSI colour.

Colour is enabled only for interactive TTYs and disabled when ``NO_COLOR`` is
set or output is redirected, so logs stay clean in files and CI.
"""

from __future__ import annotations

import os
import sys


def _enable_windows_vt() -> bool:
    """Turn on ANSI/VT processing for the Windows console so colour works there too
    (modern Windows 10+ terminals support it once the mode bit is set). Returns True
    on success; harmless no-op elsewhere."""
    if os.name != "nt":
        return False
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        # STD_OUTPUT_HANDLE = -11; ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004.
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:  # pragma: no cover - defensive; any failure just means no colour
        return False


# Colour when attached to a TTY, not disabled via NO_COLOR, and the terminal can
# render ANSI — including modern Windows consoles once VT processing is enabled.
_USE_COLOR = (
    sys.stdout.isatty()
    and os.environ.get("NO_COLOR") is None
    and (os.name != "nt" or _enable_windows_vt())
)


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
