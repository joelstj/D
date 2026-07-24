"""Subprocess helpers: streamed build commands and long-running services.

Split into two shapes:
  * ``run`` — blocking, streams a build/step's output live, returns its code.
  * ``Service`` — a supervised long-running process with its own log file and a
    cross-platform, group-aware graceful stop (CTRL_BREAK on Windows, SIGTERM to
    the process group on POSIX), escalating to kill.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from . import console
from .paths import IS_WINDOWS


def run(cmd: list[str], cwd: Path | None = None, env: dict | None = None, prefix: str = "") -> int:
    """Run a command to completion, streaming combined output. Returns exit code."""
    tag = f"[{prefix}] " if prefix else ""
    console.step(f"{tag}{' '.join(cmd)}")
    full_env = {**os.environ, **(env or {})}
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd) if cwd else None,
            env=full_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except OSError as exc:
        console.err(f"{tag}failed to start: {exc}")
        return 127
    assert proc.stdout is not None
    for line in proc.stdout:
        sys.stdout.write(f"{tag}{line}")
    return proc.wait()


class Service:
    """A supervised long-running child process writing to its own log file."""

    def __init__(self, name: str, cmd: list[str], cwd: Path, env: dict, log_path: Path):
        self.name = name
        self.cmd = cmd
        self.cwd = cwd
        self.env = env
        self.log_path = log_path
        self.proc: subprocess.Popen | None = None
        self._log_fh = None

    def start(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_fh = open(self.log_path, "w", encoding="utf-8", buffering=1)
        kwargs: dict = {
            "cwd": str(self.cwd),
            "env": {**os.environ, **self.env},
            "stdout": self._log_fh,
            "stderr": subprocess.STDOUT,
            "stdin": subprocess.DEVNULL,
        }
        if IS_WINDOWS:
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        else:
            kwargs["start_new_session"] = True
        self.proc = subprocess.Popen(self.cmd, **kwargs)

    def is_alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def returncode(self) -> int | None:
        return self.proc.poll() if self.proc else None

    def stop(self, grace: float = 6.0) -> None:
        if not self.proc:
            return
        if self.proc.poll() is not None:
            self._close_log()
            return
        try:
            if IS_WINDOWS:
                self.proc.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
            else:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
        except (ProcessLookupError, OSError):
            pass
        deadline = time.time() + grace
        while time.time() < deadline and self.proc.poll() is None:
            time.sleep(0.15)
        if self.proc.poll() is None:
            try:
                if IS_WINDOWS:
                    self.proc.kill()
                else:
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass
        self._close_log()

    def _close_log(self) -> None:
        if self._log_fh:
            try:
                self._log_fh.close()
            except OSError:
                pass
            self._log_fh = None
