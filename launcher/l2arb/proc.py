"""Subprocess helpers: streamed build commands and long-running services.

Split into two shapes:
  * ``run`` — blocking, streams a build/step's output live, returns its code.
  * ``Service`` — a supervised long-running process with its own log file and a
    cross-platform, group-aware graceful stop (CTRL_BREAK on Windows, SIGTERM to
    the process group on POSIX), escalating to kill.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from . import console
from .paths import IS_WINDOWS


def _resolve(cmd: list[str], env: dict) -> list[str]:
    """Resolve ``cmd[0]`` to the full path ``PATH`` search actually finds.

    Windows' ``CreateProcess`` (what ``subprocess.Popen`` calls without
    ``shell=True``) only auto-appends ``.exe`` to an extension-less name — unlike
    ``cmd.exe``, it never consults ``PATHEXT``. Tools that ship as ``.cmd``/``.bat``
    shims (pnpm, corepack, npm, …) are found fine by ``shutil.which`` (which does
    check ``PATHEXT``) but then fail to launch with ``WinError 2`` if the bare name
    is handed to ``Popen`` directly. Resolving here fixes every caller at once.
    """
    if not cmd:
        return cmd
    found = shutil.which(cmd[0], path=env.get("PATH"))
    return [found, *cmd[1:]] if found else cmd


def run(cmd: list[str], cwd: Path | None = None, env: dict | None = None, prefix: str = "") -> int:
    """Run a command to completion, streaming combined output. Returns exit code.

    ``encoding``/``errors`` are pinned rather than left to default to
    ``text=True``'s ``locale.getpreferredencoding()`` — on Windows that's often a
    legacy codepage (cp1252, …), and build tools (pnpm, tsup, …) emit UTF-8, so
    the pipe read can hit a byte cp1252 has no mapping for (e.g. 0x8f) and raise
    ``UnicodeDecodeError`` mid-build. Pinning UTF-8 decodes real tool output
    correctly; ``errors="replace"`` is a backstop against any other stray byte.
    """
    tag = f"[{prefix}] " if prefix else ""
    console.step(f"{tag}{' '.join(cmd)}")
    full_env = {**os.environ, **(env or {})}
    try:
        proc = subprocess.Popen(
            _resolve(cmd, full_env),
            cwd=str(cwd) if cwd else None,
            env=full_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except OSError as exc:
        console.err(f"{tag}failed to start: {exc}")
        return 127
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            sys.stdout.write(f"{tag}{line}")
    finally:
        # Deterministic close instead of leaving it to GC: `Popen(stdout=PIPE)`
        # hands back a real TextIOWrapper the caller owns and is expected to
        # close. Left implicit, cpython's refcounting GC does eventually close
        # it (this was not a real fd pileup), but it fires a ResourceWarning
        # and leaves cleanup to GC timing rather than this function's own
        # control flow — including on the exception path (e.g. Ctrl-C while a
        # `pnpm install`/`cargo build` is streaming output), where nothing
        # previously closed it at all. Closing here is safe on every path: the
        # happy path already reads the pipe to EOF first, so there is nothing
        # left unread.
        proc.stdout.close()
    return proc.wait()


def capture(cmd: list[str], cwd: Path | None = None, env: dict | None = None, timeout: float | None = 30.0) -> tuple[int, str]:
    """Run a short helper command to completion, returning `(exit_code, stdout)`
    instead of streaming it — for a command whose output the caller parses
    (e.g. `discover_pools.py --json`) rather than watches. Unlike `run`, never
    raises: a failed spawn or a timeout comes back as a non-zero code with an
    explanatory string in place of stdout, so a discovery helper failing never
    takes the whole setup wizard down with it.
    """
    full_env = {**os.environ, **(env or {})}
    try:
        result = subprocess.run(
            _resolve(cmd, full_env),
            cwd=str(cwd) if cwd else None,
            env=full_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return result.returncode, result.stdout
    except OSError as exc:
        return 127, str(exc)
    except subprocess.TimeoutExpired:
        return 124, "timed out"


class Service:
    """A supervised long-running child process writing to its own log file.

    A ``Service`` is *restartable*: the health monitor (``health.py``) calls
    :meth:`restart` to recover a crashed or wedged process. The first
    :meth:`start` truncates the log; restarts append, so a run's log keeps the
    full crash/restart history for diagnosis. ``health_url`` (optional) is the
    liveness endpoint the monitor probes beyond bare process liveness.
    """

    def __init__(
        self,
        name: str,
        cmd: list[str],
        cwd: Path,
        env: dict,
        log_path: Path,
        health_url: str | None = None,
    ):
        self.name = name
        self.cmd = cmd
        self.cwd = cwd
        self.env = env
        self.log_path = log_path
        self.health_url = health_url
        self.proc: subprocess.Popen | None = None
        self._log_fh = None
        self._started_once = False

    def start(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        # Truncate on the first start of a run; append on every restart so the
        # log preserves the history that explains why a restart was needed.
        mode = "a" if self._started_once else "w"
        self._log_fh = open(self.log_path, mode, encoding="utf-8", buffering=1)
        full_env = {**os.environ, **self.env}
        kwargs: dict = {
            "cwd": str(self.cwd),
            "env": full_env,
            "stdout": self._log_fh,
            "stderr": subprocess.STDOUT,
            "stdin": subprocess.DEVNULL,
        }
        if IS_WINDOWS:
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        else:
            kwargs["start_new_session"] = True
        try:
            self.proc = subprocess.Popen(_resolve(self.cmd, full_env), **kwargs)
        except BaseException:
            # A failed spawn (e.g. missing binary → OSError) must not leak the log
            # file handle we just opened; nothing else will close it since
            # `stop()` early-returns while `self.proc` is None.
            self._close_log()
            raise
        self._started_once = True

    def restart(self) -> None:
        """Stop the current process (if any) and start a fresh one."""
        self.stop()
        self.start()

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.proc else None

    def tail_log(self, n: int = 3) -> list[str]:
        """Return the last ``n`` non-blank lines of the log, for diagnostics."""
        try:
            with open(self.log_path, encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
        except OSError:
            return []
        return [ln.rstrip("\n") for ln in lines if ln.strip()][-n:]

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
            # Reap the killed child so it does not linger as a <defunct> zombie.
            # The SIGTERM wait-loop above reaps via poll() on a clean exit; the
            # SIGKILL escalation path must reap explicitly (SIGKILL can't be
            # caught, so the process is already dead — wait() returns promptly).
            try:
                self.proc.wait(timeout=grace)
            except (subprocess.TimeoutExpired, ValueError, OSError):
                pass
        self._close_log()

    def _close_log(self) -> None:
        if self._log_fh:
            try:
                self._log_fh.close()
            except OSError:
                pass
            self._log_fh = None
