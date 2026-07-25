"""Detect (and, on Windows, install) the toolchains each component needs.

The merged bot spans three ecosystems:

  engine      Python 3.11–3.12  (venv + pip / uv)   → detection engine
  dashboard   Node >= 20 + pnpm (or npm)            → REST/WS API + React UI
  ingestion   Rust (cargo) >= 1.94                  → l2-ingest binary

On Windows the installer can satisfy missing toolchains automatically via
``winget`` (Python.Python.3.12, OpenJS.NodeJS.LTS, Rustlang.Rustup). On
Linux/macOS we detect and print precise guidance rather than touching the
system — installing a system compiler behind the user's back is not our call.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass

from . import console
from .paths import IS_WINDOWS

_VERSION_RE = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")


@dataclass
class ToolStatus:
    name: str
    path: str | None
    version: tuple[int, ...] | None
    ok: bool
    note: str = ""

    @property
    def version_str(self) -> str:
        return ".".join(str(p) for p in self.version) if self.version else "not found"


def which(cmd: str) -> str | None:
    return shutil.which(cmd)


def _run(cmd: list[str]) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return p.returncode, (p.stdout + p.stderr).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)


def _version_of(cmd: list[str]) -> tuple[int, ...] | None:
    rc, out = _run(cmd)
    if rc != 0:
        return None
    m = _VERSION_RE.search(out)
    if not m:
        return None
    return tuple(int(g) for g in m.groups() if g is not None)


def _ge(v: tuple[int, ...] | None, minimum: tuple[int, ...]) -> bool:
    return v is not None and v >= minimum


def detect_node() -> ToolStatus:
    path = which("node")
    v = _version_of(["node", "--version"]) if path else None
    return ToolStatus("node", path, v, _ge(v, (20,)), "need >= 20")


def detect_pnpm() -> ToolStatus:
    path = which("pnpm")
    v = _version_of(["pnpm", "--version"]) if path else None
    return ToolStatus("pnpm", path, v, path is not None, "or npm")


def detect_npm() -> ToolStatus:
    path = which("npm")
    v = _version_of(["npm", "--version"]) if path else None
    return ToolStatus("npm", path, v, path is not None)


def detect_cargo() -> ToolStatus:
    path = which("cargo")
    v = _version_of(["cargo", "--version"]) if path else None
    return ToolStatus("cargo", path, v, _ge(v, (1, 94)), "need >= 1.94 (rust-toolchain pins 1.94.1)")


def detect_uv() -> ToolStatus:
    path = which("uv")
    v = _version_of(["uv", "--version"]) if path else None
    return ToolStatus("uv", path, v, path is not None, "optional, speeds up engine install")


def find_engine_python() -> str | None:
    """Locate a Python 3.11 or 3.12 interpreter for the engine venv.

    Never returns the frozen launcher executable — the engine needs a real
    CPython the venv module can clone.
    """
    candidates: list[list[str]] = []
    if IS_WINDOWS:
        candidates += [["py", "-3.12"], ["py", "-3.11"]]
    candidates += [
        ["python3.12"],
        ["python3.11"],
        ["python3"],
        ["python"],
    ]
    for cmd in candidates:
        exe = which(cmd[0])
        if not exe:
            continue
        v = _version_of(cmd + ["--version"])
        if v and v[0] == 3 and v[1] in (11, 12):
            # Return an invocation string the caller can split.
            return " ".join(cmd)
    return None


def detect_all() -> dict[str, ToolStatus]:
    return {
        "node": detect_node(),
        "pnpm": detect_pnpm(),
        "npm": detect_npm(),
        "cargo": detect_cargo(),
        "uv": detect_uv(),
    }


def report(statuses: dict[str, ToolStatus]) -> None:
    console.banner("Toolchain check")
    py = find_engine_python()
    console.info(f"engine python : {py or 'MISSING (need Python 3.11 or 3.12)'}")
    for key in ("node", "pnpm", "npm", "cargo", "uv"):
        s = statuses[key]
        mark = "✓" if s.ok else ("·" if key in ("uv", "npm", "pnpm") else "✗")
        extra = f"  ({s.note})" if s.note and not s.ok else ""
        console.info(f"{mark} {s.name:6}: {s.version_str}{extra}")


# ── Windows auto-install via winget ──────────────────────────────────────────
_WINGET_IDS = {
    "python": "Python.Python.3.12",
    "node": "OpenJS.NodeJS.LTS",
    "rust": "Rustlang.Rustup",
}


def _winget_install(pkg_id: str) -> bool:
    if not which("winget"):
        console.warn("winget not available; cannot auto-install")
        return False
    console.step(f"winget install {pkg_id}")
    rc, out = _run(
        [
            "winget",
            "install",
            "--id",
            pkg_id,
            "-e",
            "--source",
            "winget",
            "--accept-package-agreements",
            "--accept-source-agreements",
            "--disable-interactivity",
        ]
    )
    if rc == 0:
        console.ok(f"installed {pkg_id}")
        return True
    console.warn(f"winget install {pkg_id} exited {rc}: {out[:200]}")
    return False


def merge_path(existing: str, extra, *, windows: bool = IS_WINDOWS, sep: str = os.pathsep) -> str:
    """Append `extra` directories to a PATH string, dropping duplicates and empties
    (case-insensitively on Windows) while preserving first-seen order. The pure core
    of [`refresh_process_path`]."""
    seen: set[str] = set()
    out: list[str] = []
    for part in list(existing.split(sep)) + list(extra):
        part = part.strip()
        if not part:
            continue
        key = part.rstrip("\\/")
        key = key.lower() if windows else key
        if key in seen:
            continue
        seen.add(key)
        out.append(part)
    return sep.join(out)


def _windows_registry_path() -> list[str]:
    """PATH entries from the Windows registry (user + machine) — where winget writes
    a freshly-installed tool's directory. Empty on non-Windows or on any error."""
    if not IS_WINDOWS:
        return []
    try:
        import winreg  # type: ignore
    except ImportError:
        return []
    dirs: list[str] = []
    keys = (
        (winreg.HKEY_CURRENT_USER, "Environment"),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
        ),
    )
    for root, sub in keys:
        try:
            with winreg.OpenKey(root, sub) as k:
                val, _ = winreg.QueryValueEx(k, "Path")
        except OSError:
            continue
        dirs.extend(os.path.expandvars(p) for p in val.split(os.pathsep) if p)
    return dirs


def refresh_process_path() -> bool:
    """Pull newly-installed tool directories from the registry into this process's
    ``PATH`` so a tool winget *just* installed is found without reopening the shell —
    the usual reason a first-run build fails right after a successful install.
    Returns True if ``PATH`` changed."""
    extra = _windows_registry_path()
    if not extra:
        return False
    before = os.environ.get("PATH", "")
    after = merge_path(before, extra)
    if after == before:
        return False
    os.environ["PATH"] = after
    return True


def ensure_windows_prereqs(need_rust: bool = True, need_engine: bool = True) -> None:
    """Best-effort install of missing toolchains on Windows via winget, then refresh
    the process ``PATH`` so this same run can use them."""
    if not IS_WINDOWS:
        return
    installed = False
    if need_engine and not find_engine_python():
        installed |= _winget_install(_WINGET_IDS["python"])
    if not detect_node().ok:
        installed |= _winget_install(_WINGET_IDS["node"])
    if need_rust and not detect_cargo().ok:
        installed |= _winget_install(_WINGET_IDS["rust"])

    if installed and refresh_process_path():
        console.info("refreshed PATH with newly-installed tools (no restart needed)")

    # If a required tool is still not visible, winget's PATH update needs a fresh
    # session — tell the user plainly instead of failing the build cryptically.
    missing = []
    if need_engine and not find_engine_python():
        missing.append("Python 3.11/3.12")
    if not detect_node().ok:
        missing.append("Node ≥ 20")
    if need_rust and not detect_cargo().ok:
        missing.append("Rust (cargo)")
    if installed and missing:
        console.warn(
            "installed toolchain(s), but "
            + ", ".join(missing)
            + " aren't on PATH yet. Close this window and run L2ArbBot again."
        )
