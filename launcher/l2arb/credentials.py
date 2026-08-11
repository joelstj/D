"""Persistent credential store — a local SQLite database in the state dir.

Everything the guided setup collects (RPC endpoints, WebSocket URLs, API keys,
the wallet address profit is paid to, tuning overrides) lands here, so the app
asks once and remembers forever. The database is **created on first use** at
``<state dir>/credentials.db`` — a dedicated file, not shared with any other
store the app keeps (``state.json`` holds only build metadata).

Trust model — deliberately the same as the ``.env`` file this replaces, no
weaker and no stronger:

* Values are stored **as plaintext**. Adding encryption would need a key, and
  the only keys available are either a passphrase the user retypes on every
  launch (defeating "ask once") or a machine-derived key stored beside the
  database (trivially reversible, i.e. security theatre). ``.env`` — which this
  supersedes — is plaintext for exactly the same reason, so this is not a
  regression, and pretending otherwise would be worse than being explicit.
* The file is created **owner-read/write only** (0600) wherever the platform
  supports it.
* ``.l2arb/`` is git-ignored, so the database is never committed.
* Values flagged ``is_secret`` are **never** printed, logged, or echoed — the
  console and health report only ever show :func:`mask`'s redacted form.

What is deliberately **not** stored here: a wallet private key or seed phrase.
The detection stack holds no keys and signs nothing (root ``CLAUDE.md`` §2
invariant 2/3), so no private key is ever needed to reach a fully-healthy
install; the one place the repo uses one is the separate, human-authorised
Hardhat deploy step, which reads its own ``contracts/.env``. Collecting a
signing key into a store the running services can read would hand the loop the
custody it is architected not to have — the same request §16 already declined
and recorded. :mod:`l2arb.requirements` says so in the user-facing text rather
than silently omitting it.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .paths import Layout

#: Bumped only for a breaking change to the table shapes below.
SCHEMA_VERSION = 1

DB_FILENAME = "credentials.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS credentials (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    category   TEXT NOT NULL DEFAULT '',
    is_secret  INTEGER NOT NULL DEFAULT 0,
    source     TEXT NOT NULL DEFAULT 'wizard',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS health_runs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ran_at     TEXT NOT NULL,
    mode       TEXT NOT NULL,
    percent    REAL NOT NULL,
    satisfied  INTEGER NOT NULL,
    total      INTEGER NOT NULL
);
"""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def mask(value: str) -> str:
    """A redacted rendering of a secret, safe to print anywhere.

    Keeps just enough to let the user recognise *which* credential it is (a
    couple of leading characters and the length) without disclosing it. Short
    values reveal nothing at all — with four characters or fewer, a prefix would
    be most of the secret.
    """
    if not value:
        return "(empty)"
    if len(value) <= 4:
        return "•" * len(value)
    return f"{value[:2]}{'•' * min(len(value) - 4, 20)}{value[-2:]}"


def mask_url(value: str) -> str:
    """Redact the credential-bearing part of an RPC URL, keeping the host.

    Provider endpoints carry the API key as the **last path segment**
    (``https://arb-mainnet.g.alchemy.com/v2/<KEY>``). Showing scheme, host and
    the structural part of the path lets the operator confirm at a glance which
    endpoint this is; only the final segment — the actual secret — is hidden,
    and it is hidden *whole*, since revealing a prefix and suffix of the one
    piece that is genuinely the credential defeats the point.
    """
    if "://" not in value:
        return mask(value)
    scheme, rest = value.split("://", 1)
    host, slash, path = rest.partition("/")
    if not slash or not path:
        return f"{scheme}://{host}"
    head, _, key = path.rpartition("/")
    hidden = "•" * min(max(len(key), 3), 12)
    return f"{scheme}://{host}/{head}/{hidden}" if head else f"{scheme}://{host}/{hidden}"


@dataclass(frozen=True)
class Entry:
    """One stored credential and its bookkeeping."""

    key: str
    value: str
    category: str
    is_secret: bool
    source: str
    updated_at: str

    def display(self) -> str:
        """The value as it is safe to show on screen."""
        if not self.is_secret:
            return self.value
        return mask_url(self.value) if "://" in self.value else mask(self.value)


class CredentialStore:
    """Thin, synchronous wrapper over the SQLite file. Not thread-shared."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existed = self.path.exists()
        self._conn = sqlite3.connect(str(self.path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.execute(
            "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        self._conn.execute(
            "INSERT OR IGNORE INTO meta (key, value) VALUES ('created_at', ?)", (_now(),)
        )
        self._conn.commit()
        if not existed:
            self._restrict_permissions()

    def _restrict_permissions(self) -> None:
        """Owner-only (0600) where the platform supports it.

        On Windows ``chmod`` cannot express a real ACL, so this is a no-op there
        and the file inherits the user's profile directory permissions — which
        already exclude other non-administrator users. Best-effort by design:
        an exotic filesystem refusing the call must not stop the app starting.
        """
        with contextlib.suppress(OSError, NotImplementedError):
            os.chmod(self.path, 0o600)

    # ── lifecycle ────────────────────────────────────────────────────────────
    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "CredentialStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ── credentials ──────────────────────────────────────────────────────────
    def set(
        self,
        key: str,
        value: str,
        *,
        category: str = "",
        is_secret: bool = False,
        source: str = "wizard",
    ) -> None:
        """Insert or replace one credential."""
        self._conn.execute(
            """
            INSERT INTO credentials (key, value, category, is_secret, source, updated_at)
                 VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                 value=excluded.value, category=excluded.category,
                 is_secret=excluded.is_secret, source=excluded.source,
                 updated_at=excluded.updated_at
            """,
            (key, value, category, int(is_secret), source, _now()),
        )
        self._conn.commit()

    def get(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM credentials WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def delete(self, key: str) -> bool:
        cur = self._conn.execute("DELETE FROM credentials WHERE key = ?", (key,))
        self._conn.commit()
        return cur.rowcount > 0

    def entries(self) -> list[Entry]:
        rows = self._conn.execute(
            "SELECT key, value, category, is_secret, source, updated_at"
            "  FROM credentials ORDER BY category, key"
        ).fetchall()
        return [
            Entry(
                key=r["key"],
                value=r["value"],
                category=r["category"],
                is_secret=bool(r["is_secret"]),
                source=r["source"],
                updated_at=r["updated_at"],
            )
            for r in rows
        ]

    def as_dict(self) -> dict[str, str]:
        return {e.key: e.value for e in self.entries()}

    # ── health history ───────────────────────────────────────────────────────
    def record_health(self, *, mode: str, percent: float, satisfied: int, total: int) -> None:
        """Append one health-check result, so `doctor` can show whether the
        install is trending toward ready and the wizard can tell a first run
        from a re-run."""
        self._conn.execute(
            "INSERT INTO health_runs (ran_at, mode, percent, satisfied, total) VALUES (?, ?, ?, ?, ?)",
            (_now(), mode, float(percent), int(satisfied), int(total)),
        )
        self._conn.commit()

    def last_health(self) -> dict | None:
        row = self._conn.execute(
            "SELECT ran_at, mode, percent, satisfied, total FROM health_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def created_at(self) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key = 'created_at'").fetchone()
        return row["value"] if row else None


def db_path(lo: Layout) -> Path:
    return lo.state_dir / DB_FILENAME


def open_store(lo: Layout) -> CredentialStore:
    """Open (creating on first use) the credential database for this install."""
    lo.ensure_state_dirs()
    return CredentialStore(db_path(lo))


@contextlib.contextmanager
def store(lo: Layout) -> Iterator[CredentialStore]:
    """Context-managed :func:`open_store`."""
    st = open_store(lo)
    try:
        yield st
    finally:
        st.close()
