"""Continuous health monitor with self-diagnosis and self-fix retry logic.

This is the launcher's live supervisor. After the stack is started and health-
gated (``run.py``), it takes over the terminal with a compact **heads-up
display** (HUD) and, every ``interval`` seconds:

  1. **probes** each service — process liveness *and*, where one exists, its
     HTTP ``/health`` endpoint (engine ``:8080``, ingestion ``:9100``, dashboard
     ``:<port>``);
  2. **diagnoses** any fault (process exited with code N + last log lines, or
     "up but endpoint not responding"), and
  3. **self-fixes** by restarting a crashed/wedged process, with exponential
     backoff and a bounded restart budget — giving up cleanly (never hot-looping)
     when a service can't be recovered.

Design for testability: the decision logic (:class:`ServiceHealth.evaluate`),
backoff, diagnostics, and the HUD renderer are all **pure** functions of an
injected clock + probe result, so they are unit-tested with fakes. The
:class:`HealthMonitor` driver is a thin loop over them with injectable clock,
sleep, prober, and output sink.

Safety: "self-fix" here means **supervised restart of detection/UI infrastructure
processes only**. It never signs, submits, re-broadcasts, or deploys anything —
execution stays paper-by-default and human-gated (root CLAUDE.md invariant 3).
The dashboard is restarted in exactly the mode it was launched with (paper).
"""

from __future__ import annotations

import os
import sys
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Protocol

from . import console


class Phase(str, Enum):
    """Lifecycle phase of a single monitored service."""

    STARTING = "starting"   # freshly (re)started, not yet seen healthy
    HEALTHY = "healthy"     # process up and endpoint responsive (or no endpoint)
    DEGRADED = "degraded"   # process up but endpoint not responding past grace
    DOWN = "down"           # process exited unexpectedly
    FAILED = "failed"       # restart budget exhausted — given up (terminal)


class Action(str, Enum):
    """What the driver should do about a service after an ``evaluate``."""

    NONE = "none"
    RESTART = "restart"
    GIVE_UP = "give_up"


@dataclass(frozen=True)
class Probe:
    """A single observation of a service."""

    alive: bool
    responsive: bool | None  # None => no health endpoint; liveness is the only signal
    latency_ms: float | None = None
    detail: str = ""


@dataclass(frozen=True)
class MonitorPolicy:
    """Tunables for the self-fix state machine (seconds unless noted)."""

    startup_grace: float = 45.0        # unresponsive-but-booting patience (first boot only)
    degraded_restart_after: float = 20.0  # up-but-unresponsive tolerance before restart
    max_restarts: int = 5              # restart budget before giving up
    backoff_base: float = 1.0          # first backoff; doubles each restart
    backoff_cap: float = 30.0          # backoff ceiling
    stable_reset_after: float = 120.0  # healthy this long forgives past restarts
    probe_timeout: float = 2.0         # per-probe HTTP timeout
    interval: float = 1.5              # tick / HUD-refresh cadence


def backoff_delay(attempt: int, policy: MonitorPolicy) -> float:
    """Exponential backoff for the ``attempt``-th restart, capped.

    ``attempt`` is the number of restarts already performed, so the wait before
    the very first restart is ``backoff_base``.
    """
    return min(policy.backoff_cap, policy.backoff_base * (2.0 ** max(0, attempt)))


def diagnose(*, alive: bool, returncode: int | None, responsive: bool | None, log_tail: list[str]) -> str:
    """Human-readable self-diagnosis of a fault (pure)."""
    if not alive:
        base = f"process exited (code {returncode})" if returncode is not None else "process not running"
        if log_tail:
            base += " — last log: " + " | ".join(log_tail)
        return base
    if responsive is False:
        return "process up but health endpoint not responding"
    if responsive is None:
        return "alive (no health endpoint)"
    return "healthy"


@dataclass
class ServiceHealth:
    """Mutable per-service state + the pure self-fix decision function."""

    name: str
    has_endpoint: bool
    phase: Phase = Phase.STARTING
    started_at: float = 0.0
    ever_healthy: bool = False
    healthy_since: float | None = None
    unresponsive_since: float | None = None
    restarts: int = 0
    last_restart_at: float | None = None
    last_probe: Probe | None = None
    detail: str = ""
    pid: int | None = None
    latency_ms: float | None = None

    def mark_started(self, now: float, pid: int | None = None) -> None:
        """Record a (re)start: reset per-instance timers; keep the restart budget."""
        self.started_at = now
        self.phase = Phase.STARTING
        self.healthy_since = None
        self.unresponsive_since = None
        self.last_probe = None
        self.pid = pid

    def note_restart(self, now: float) -> None:
        self.restarts += 1
        self.last_restart_at = now

    def evaluate(self, probe: Probe, now: float, policy: MonitorPolicy) -> Action:
        """Fold a fresh ``probe`` into the state machine and return the action.

        Pure w.r.t. I/O: it only reads ``now`` and mutates ``self``. The driver
        performs whatever side effect the returned :class:`Action` implies.
        """
        self.last_probe = probe
        self.detail = probe.detail
        self.latency_ms = probe.latency_ms

        # FAILED is terminal — never churn a service we've given up on.
        if self.phase == Phase.FAILED:
            return Action.NONE

        healthy_now = probe.alive and (probe.responsive is None or probe.responsive is True)

        # 1) Process is gone.
        if not probe.alive:
            self.phase = Phase.DOWN
            self.healthy_since = None
            if self.unresponsive_since is None:
                self.unresponsive_since = now
            return self._maybe_restart(now, policy)

        # 2) Process up and healthy.
        if healthy_now:
            self.ever_healthy = True
            self.unresponsive_since = None
            if self.healthy_since is None:
                self.healthy_since = now
            self.phase = Phase.HEALTHY
            # Sustained health forgives old strikes so a long-lived service that
            # hiccuped once doesn't carry the debt forever.
            if self.restarts and (now - self.healthy_since) >= policy.stable_reset_after:
                self.restarts = 0
                self.last_restart_at = None
            return Action.NONE

        # 3) Process up but the endpoint isn't answering.
        self.healthy_since = None
        if self.unresponsive_since is None:
            self.unresponsive_since = now
        # Be patient while a never-yet-healthy process is still booting.
        if not self.ever_healthy and (now - self.started_at) < policy.startup_grace:
            self.phase = Phase.STARTING
            return Action.NONE
        self.phase = Phase.DEGRADED
        if (now - self.unresponsive_since) >= policy.degraded_restart_after:
            return self._maybe_restart(now, policy)
        return Action.NONE

    def _maybe_restart(self, now: float, policy: MonitorPolicy) -> Action:
        if self.restarts >= policy.max_restarts:
            self.phase = Phase.FAILED
            return Action.GIVE_UP
        if self.last_restart_at is not None:
            if (now - self.last_restart_at) < backoff_delay(self.restarts, policy):
                return Action.NONE  # still backing off
        return Action.RESTART


# --------------------------------------------------------------------------- #
# Probing (real network path)                                                  #
# --------------------------------------------------------------------------- #
def http_probe(url: str, timeout: float) -> tuple[bool, float | None, str]:
    """GET ``url``; return (responsive, latency_ms, detail). Never raises."""
    start = time.time()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 (localhost only)
            latency = (time.time() - start) * 1000.0
            if 200 <= resp.status < 300:
                return True, latency, f"{resp.status} in {latency:.0f}ms"
            return False, latency, f"HTTP {resp.status}"
    except urllib.error.URLError as exc:
        return False, None, f"unreachable: {getattr(exc, 'reason', exc)}"
    except (OSError, ValueError) as exc:
        return False, None, f"unreachable: {exc}"


class _ServiceLike(Protocol):
    name: str
    health_url: str | None
    def is_alive(self) -> bool: ...
    def returncode(self) -> int | None: ...
    def tail_log(self, n: int = 3) -> list[str]: ...
    def restart(self) -> None: ...
    def stop(self, grace: float = ...) -> None: ...
    @property
    def pid(self) -> int | None: ...


def probe_service(svc: _ServiceLike, timeout: float) -> Probe:
    """Observe a real service: liveness first, then its HTTP endpoint."""
    if not svc.is_alive():
        rc = svc.returncode()
        detail = diagnose(alive=False, returncode=rc, responsive=None, log_tail=svc.tail_log(3))
        return Probe(alive=False, responsive=None, detail=detail)
    url = getattr(svc, "health_url", None)
    if not url:
        return Probe(alive=True, responsive=None, detail=diagnose(alive=True, returncode=None, responsive=None, log_tail=[]))
    ok, latency, detail = http_probe(url, timeout)
    return Probe(alive=True, responsive=ok, latency_ms=latency, detail=detail)


# --------------------------------------------------------------------------- #
# HUD rendering (pure)                                                         #
# --------------------------------------------------------------------------- #
_PHASE_COLOR = {
    Phase.HEALTHY: "32",   # green
    Phase.STARTING: "33",  # yellow
    Phase.DEGRADED: "33",  # yellow
    Phase.DOWN: "31",      # red
    Phase.FAILED: "31",    # red
}


def _fmt_dur(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _color(code: str, text: str, use_color: bool) -> str:
    return f"\033[{code}m{text}\033[0m" if use_color else text


def render_hud(
    healths: list[ServiceHealth],
    now: float,
    started_at: float,
    *,
    use_color: bool,
    events: list[tuple[float, str]] = (),  # type: ignore[assignment]
) -> str:
    """Render the health HUD as a block of text (no I/O)."""
    lines: list[str] = []
    title = "L2 Arb Bot — health monitor"
    lines.append(_color("36", f"  {title}", use_color) + f"    uptime {_fmt_dur(now - started_at)}")
    lines.append("  " + "─" * 62)
    lines.append(f"  {'SERVICE':<11}{'STATE':<11}{'PID':<8}{'UPTIME':<9}{'PING':<8}{'RESTARTS'}")
    for sh in healths:
        dot = _color(_PHASE_COLOR.get(sh.phase, "0"), "●", use_color)
        state = f"{dot} {sh.phase.value}"
        state_pad = state + " " * max(0, 11 - (len(sh.phase.value) + 2))
        pid = str(sh.pid) if sh.pid else "—"
        uptime = _fmt_dur(now - sh.started_at) if sh.started_at else "—"
        ping = f"{sh.latency_ms:.0f}ms" if sh.latency_ms is not None else "—"
        lines.append(f"  {sh.name:<11}{state_pad}{pid:<8}{uptime:<9}{ping:<8}{sh.restarts}")
    if events:
        lines.append("  " + "─" * 62)
        for ts, msg in list(events)[-4:]:
            lines.append(_color("90", f"  {_fmt_dur(now - ts)} ago  {msg}", use_color))
    lines.append("  " + _color("90", "press Ctrl+C to stop", use_color))
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Driver                                                                       #
# --------------------------------------------------------------------------- #
class HealthMonitor:
    """Drives the probe → diagnose → self-fix loop and paints the HUD."""

    def __init__(
        self,
        services: list[_ServiceLike],
        policy: MonitorPolicy | None = None,
        *,
        prober: Callable[[_ServiceLike], Probe] | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        out=console,
        use_color: bool | None = None,
        is_tty: bool | None = None,
    ) -> None:
        self.services = list(services)
        self.policy = policy or MonitorPolicy()
        self.clock = clock
        self.sleep = sleep
        self.out = out
        self.prober = prober or (lambda svc: probe_service(svc, self.policy.probe_timeout))
        self.started_at = clock()
        self.health: dict[str, ServiceHealth] = {}
        for svc in self.services:
            sh = ServiceHealth(name=svc.name, has_endpoint=bool(getattr(svc, "health_url", None)))
            sh.mark_started(self.started_at, pid=getattr(svc, "pid", None))
            self.health[svc.name] = sh
        self.is_tty = is_tty if is_tty is not None else sys.stdout.isatty()
        self.use_color = (
            use_color
            if use_color is not None
            else (self.is_tty and os.environ.get("NO_COLOR") is None)
        )
        self.events: deque[tuple[float, str]] = deque(maxlen=8)
        self._hud_lines = 0

    # -- one pass ---------------------------------------------------------- #
    def tick(self, now: float | None = None) -> dict[str, Action]:
        now = self.clock() if now is None else now
        actions: dict[str, Action] = {}
        for svc in self.services:
            sh = self.health[svc.name]
            prev = sh.phase
            action = sh.evaluate(self.prober(svc), now, self.policy)
            if action == Action.RESTART:
                self._do_restart(svc, sh, now)
            elif action == Action.GIVE_UP:
                self._give_up(svc, sh)
            else:
                sh.pid = getattr(svc, "pid", sh.pid)
            if sh.phase != prev:
                self._on_phase_change(sh, prev)
            actions[svc.name] = action
        return actions

    def _do_restart(self, svc: _ServiceLike, sh: ServiceHealth, now: float) -> None:
        self._event(now, f"{sh.name}: {sh.detail or 'unhealthy'} — restarting (attempt {sh.restarts + 1})")
        try:
            svc.restart()
        except Exception as exc:  # noqa: BLE001 — recovery must not crash the monitor
            self._event(now, f"{sh.name}: restart failed: {exc}")
        sh.note_restart(now)
        sh.mark_started(now, pid=getattr(svc, "pid", None))

    def _give_up(self, svc: _ServiceLike, sh: ServiceHealth) -> None:
        self._event(self.clock(), f"{sh.name}: giving up after {sh.restarts} restarts — see logs")
        try:
            svc.stop()
        except Exception:  # noqa: BLE001
            pass

    # -- run loop ---------------------------------------------------------- #
    def run(self) -> int:
        """Block until interrupted or every service has failed. Returns exit code."""
        rc = 0
        try:
            while True:
                self.tick()
                self._render()
                if self._all_terminal():
                    self.out.err("all services failed and could not be recovered — shutting down")
                    rc = 1
                    break
                self.sleep(self.policy.interval)
        except KeyboardInterrupt:
            self.out.step("stopping services…")
        finally:
            for svc in reversed(self.services):
                try:
                    svc.stop()
                except Exception:  # noqa: BLE001
                    pass
            self.out.ok("all services stopped")
        return rc

    def _all_terminal(self) -> bool:
        return bool(self.health) and all(sh.phase == Phase.FAILED for sh in self.health.values())

    # -- output ------------------------------------------------------------ #
    def _event(self, now: float, msg: str) -> None:
        self.events.append((now, msg))
        if not self.is_tty:
            self.out.warn(msg)

    def _on_phase_change(self, sh: ServiceHealth, prev: Phase) -> None:
        if self.is_tty:
            return  # the HUD already reflects the new phase
        if sh.phase == Phase.HEALTHY:
            self.out.ok(f"{sh.name}: {prev.value} → healthy")

    def _render(self) -> None:
        if not self.is_tty:
            return
        text = render_hud(
            list(self.health.values()),
            self.clock(),
            self.started_at,
            use_color=self.use_color,
            events=list(self.events),
        )
        out = sys.stdout
        if self._hud_lines:
            out.write(f"\033[{self._hud_lines}A")  # move cursor up over the old block
        out.write("\033[J")  # clear from cursor to end of screen (handles shrink)
        out.write(text + "\n")
        out.flush()
        self._hud_lines = text.count("\n") + 1
