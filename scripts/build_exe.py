#!/usr/bin/env python3
"""Build the L2ArbBot executable.

Two steps:
  1. Stage a **clean** copy of the four component source trees into
     ``launcher/build/payload/`` (excluding build artifacts, node_modules, target,
     venvs, .git, …). This is what the exe unpacks and installs on first run.
  2. Run PyInstaller against ``scripts/l2arbbot.spec`` to produce a single-file
     executable at ``launcher/dist/L2ArbBot`` (``.exe`` on Windows).

Cross-platform: on Windows it emits ``L2ArbBot.exe``; on Linux/macOS it emits a
native binary (useful for smoke-testing the bundling + payload logic). Requires
PyInstaller (``pip install pyinstaller``).

Usage:
    python scripts/build_exe.py [--clean]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def _force_utf8_streams() -> None:
    """Reconfigure stdout/stderr to UTF-8 so the arrows/checkmarks below can't
    raise UnicodeEncodeError under a non-UTF-8 codepage — e.g. Windows falling
    back to cp1252 when this script's output is piped through PowerShell's
    ``| Out-Host``, as ``build_windows_exe.ps1`` does."""
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # pragma: no cover - defensive; never block the build
                pass


_force_utf8_streams()

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "launcher"
BUILD = LAUNCHER / "build"
PAYLOAD = BUILD / "payload"
DIST = LAUNCHER / "dist"
SPEC = ROOT / "scripts" / "l2arbbot.spec"

COMPONENTS = ("engine", "ingestion", "contracts", "dashboard")

# Never ship build artifacts, dependency caches, or VCS metadata inside the exe.
IGNORE = shutil.ignore_patterns(
    "node_modules",
    "target",
    "dist",
    "build",
    ".venv",
    "venv",
    "__pycache__",
    "*.pyc",
    ".git",
    ".github",
    ".l2arb",
    "out",
    "cache",
    "cache_hardhat",
    "artifacts",
    "broadcast",
    "typechain-types",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".hypothesis",
    "htmlcov",
    ".coverage",
)


def stage_payload() -> None:
    print(f"[build_exe] staging clean payload → {PAYLOAD}")
    if PAYLOAD.exists():
        shutil.rmtree(PAYLOAD)
    PAYLOAD.mkdir(parents=True, exist_ok=True)
    for comp in COMPONENTS:
        src = ROOT / comp
        if not src.exists():
            print(f"[build_exe] WARNING: component '{comp}' not found at {src}; skipping")
            continue
        dst = PAYLOAD / comp
        shutil.copytree(src, dst, ignore=IGNORE)
        n = sum(1 for _ in dst.rglob("*") if _.is_file())
        print(f"[build_exe]   {comp}: {n} files")


def have_pyinstaller() -> bool:
    try:
        subprocess.run(
            [sys.executable, "-m", "PyInstaller", "--version"],
            capture_output=True,
            check=True,
        )
        return True
    except (subprocess.CalledProcessError, OSError):
        return False


def run_pyinstaller(clean: bool) -> int:
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        str(SPEC),
        "--noconfirm",
        "--distpath",
        str(DIST),
        "--workpath",
        str(BUILD / "pyi"),
    ]
    if clean:
        cmd.append("--clean")
    print(f"[build_exe] {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=str(ROOT)).returncode


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the L2ArbBot executable")
    ap.add_argument("--clean", action="store_true", help="clean PyInstaller caches first")
    ap.add_argument("--stage-only", action="store_true", help="only stage the payload, do not run PyInstaller")
    args = ap.parse_args()

    stage_payload()
    if args.stage_only:
        return 0

    if not have_pyinstaller():
        print("[build_exe] ERROR: PyInstaller not installed. Run: pip install pyinstaller", file=sys.stderr)
        return 2

    rc = run_pyinstaller(args.clean)
    if rc == 0:
        exe = DIST / ("L2ArbBot.exe" if sys.platform == "win32" else "L2ArbBot")
        print(f"\n[build_exe] ✓ built: {exe}")
        print("[build_exe]   run it to install-if-needed and open the dashboard.")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
