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


def ensure_windows_prereqs(need_rust: bool = True, need_engine: bool = True) -> None:
    """Best-effort install of missing toolchains on Windows via winget."""
    if not IS_WINDOWS:
        return
    if need_engine and not find_engine_python():
        _winget_install(_WINGET_IDS["python"])
    if not detect_node().ok:
        _winget_install(_WINGET_IDS["node"])
    if need_rust and not detect_cargo().ok:
        _winget_install(_WINGET_IDS["rust"])
