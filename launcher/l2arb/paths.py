"""Filesystem + platform layout for the L2 Arbitrage Bot launcher.

Centralises *where everything lives* so the rest of the launcher never guesses:

  workspace root      the dir that contains engine/ ingestion/ contracts/ dashboard/.
                      In a dev checkout this is the repo root (parent of launcher/).
                      In a frozen (.exe) install it is the per-user install dir,
                      populated on first run from the payload bundled in the exe.
  state dir           <workspace>/.l2arb — venv, generated config, logs, pids, marker.

Everything here is stdlib-only so the module bundles cleanly into a PyInstaller
one-file executable with no third-party imports.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "L2ArbBot"
COMPONENTS = ("engine", "ingestion", "contracts", "dashboard")

IS_WINDOWS = os.name == "nt"
IS_MACOS = sys.platform == "darwin"
EXE_SUFFIX = ".exe" if IS_WINDOWS else ""


def is_frozen() -> bool:
    """True when running from a PyInstaller-built executable."""
    return getattr(sys, "frozen", False)


def bundle_dir() -> Path | None:
    """The dir PyInstaller unpacks bundled data into (``sys._MEIPASS``), or None."""
    mei = getattr(sys, "_MEIPASS", None)
    return Path(mei) if mei else None


def default_install_root() -> Path:
    """Per-user install location used when running as a frozen executable."""
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP_NAME
    if IS_MACOS:
        return Path.home() / "Library" / "Application Support" / APP_NAME
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "l2arb-bot"


def workspace_root() -> Path:
    """Resolve the workspace that holds the four components.

    Order of precedence:
      1. ``L2ARB_HOME`` env override (explicit).
      2. Frozen build → the per-user install root.
      3. Dev checkout → the repo root (parent of this file's ``launcher/`` dir).
    """
    override = os.environ.get("L2ARB_HOME")
    if override:
        return Path(override).expanduser().resolve()
    if is_frozen():
        return default_install_root()
    # launcher/l2arb/paths.py -> launcher/l2arb -> launcher -> <repo root>
    return Path(__file__).resolve().parents[2]


class Layout:
    """Concrete resolved paths for one workspace."""

    def __init__(self, root: Path):
        self.root = root

    # component roots ---------------------------------------------------------
    @property
    def engine(self) -> Path:
        return self.root / "engine"

    @property
    def ingestion(self) -> Path:
        return self.root / "ingestion"

    @property
    def contracts(self) -> Path:
        return self.root / "contracts"

    @property
    def dashboard(self) -> Path:
        return self.root / "dashboard"

    # build outputs -----------------------------------------------------------
    @property
    def ingest_binary(self) -> Path:
        return self.ingestion / "target" / "release" / f"l2-ingest{EXE_SUFFIX}"

    @property
    def dashboard_backend_entry(self) -> Path:
        return self.dashboard / "backend" / "dist" / "index.js"

    @property
    def frontend_dist(self) -> Path:
        return self.dashboard / "frontend" / "dist"

    # state -------------------------------------------------------------------
    @property
    def state_dir(self) -> Path:
        return self.root / ".l2arb"

    @property
    def venv_dir(self) -> Path:
        return self.state_dir / "venv-engine"

    def venv_python(self) -> Path:
        if IS_WINDOWS:
            return self.venv_dir / "Scripts" / "python.exe"
        return self.venv_dir / "bin" / "python"

    @property
    def state_file(self) -> Path:
        return self.state_dir / "state.json"

    @property
    def config_toml(self) -> Path:
        return self.state_dir / "config.toml"

    @property
    def logs_dir(self) -> Path:
        return self.state_dir / "logs"

    @property
    def run_dir(self) -> Path:
        return self.state_dir / "run"

    def ensure_state_dirs(self) -> None:
        for d in (self.state_dir, self.logs_dir, self.run_dir):
            d.mkdir(parents=True, exist_ok=True)


def layout() -> Layout:
    return Layout(workspace_root())
