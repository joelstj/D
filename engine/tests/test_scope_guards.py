"""Static guards that enforce the project's hard scope rules on runtime code.

These fail the moment any ``src/l2arb`` module drifts toward transaction signing/
submission (out of scope per CLAUDE.md §1 / ADR-001) or pulls synthetic/test data
into a runtime path (forbidden by docs/DATA_INTEGRITY.md). They are cheap and run
from the very first iteration so the constitution is enforced, not just written.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

SRC = Path(__file__).resolve().parent.parent / "src" / "l2arb"

# Identifier-shaped tokens that indicate signing or broadcasting a transaction.
# Chosen so they match code, not prose (e.g. they won't match the word "signing").
SIGNING_TOKENS = (
    "send_raw_transaction",
    "sendRawTransaction",
    "sign_transaction",
    "signTransaction",
    "sign_message",
    "from_key(",
)

# Tokens indicating synthetic/test data being imported into runtime modules.
SYNTHETIC_TOKENS = (
    "import faker",
    "from faker",
    "from tests",
    "import tests",
    "synthetic_data",
)


def _runtime_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def test_there_is_runtime_code_to_scan() -> None:
    assert _runtime_files(), "expected src/l2arb to contain modules"


@pytest.mark.parametrize("token", SIGNING_TOKENS)
def test_no_signing_or_broadcast_in_runtime(token: str) -> None:
    offenders = [
        p.relative_to(SRC).as_posix()
        for p in _runtime_files()
        if token in p.read_text(encoding="utf-8")
    ]
    assert not offenders, f"signing/broadcast token {token!r} found in: {offenders}"


@pytest.mark.parametrize("token", SYNTHETIC_TOKENS)
def test_no_synthetic_data_in_runtime(token: str) -> None:
    offenders = [
        p.relative_to(SRC).as_posix()
        for p in _runtime_files()
        if token in p.read_text(encoding="utf-8")
    ]
    assert not offenders, f"synthetic-data token {token!r} found in: {offenders}"


def test_runtime_detection_path_does_not_import_backtest() -> None:
    """Offline backtest/analytics must never be reachable from runtime (T-0905).

    Every ``src/l2arb`` module *except* those under ``backtest/`` is forbidden from
    importing the backtest package, so replay/metrics code can never leak into the
    live detection or API path.
    """
    offenders = [
        p.relative_to(SRC).as_posix()
        for p in _runtime_files()
        if "backtest" not in p.parts and "l2arb.backtest" in p.read_text(encoding="utf-8")
    ]
    assert not offenders, f"runtime modules import backtest: {offenders}"
