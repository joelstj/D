"""The health check: what does this install still need before it really works?

Run on every launch. For each requirement in :mod:`l2arb.requirements` it
resolves a value, validates it, and — for endpoints — optionally *proves* it by
talking to the network, then scores the result.

Resolution order matches the precedence the rest of the repo documents for
configuration (root ``CLAUDE.md`` §3):

    1. a real environment variable   (what your shell or a wrapper injected)
    2. the credential database       (what you told the guided setup)
    3. the built-in default          (optional tuning knobs only)

Environment beating the database matters: an operator who exports a variable for
one run expects it to win. The source of every resolved value is reported, so a
stale environment variable silently shadowing a stored one is visible rather
than mysterious.

**Endpoints are proved, not just parsed.** A syntactically perfect RPC URL that
404s, or that points at the wrong network, looks identical to a good one until
the bot silently reports nothing. The probe issues a real ``eth_chainId`` call
and checks the answer against the chain the endpoint was entered for, which
catches both a dead endpoint and the very common paste-into-the-wrong-slot
mistake. Probing is opt-out (``probe=False``) so tests and offline launches stay
instant.
"""

from __future__ import annotations

import json
import os
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from . import credentials, prereqs, requirements, state, textio
from .paths import Layout
from .requirements import BLOCKING, OPTIONAL, RECOMMENDED, Requirement

# ── Item statuses ────────────────────────────────────────────────────────────
OK = "ok"
MISSING = "missing"
INVALID = "invalid"
UNREACHABLE = "unreachable"
WRONG_CHAIN = "wrong_chain"

#: Statuses that mean "this requirement is satisfied".
_SATISFIED = frozenset({OK})

# ── Value sources ────────────────────────────────────────────────────────────
SRC_ENV = "environment"
SRC_DB = "database"
SRC_DEFAULT = "default"
SRC_NONE = "-"

PROBE_TIMEOUT = 6.0


@dataclass
class ItemResult:
    """The outcome of checking one requirement."""

    requirement: Requirement
    status: str
    value: str | None = None
    source: str = SRC_NONE
    detail: str = ""

    @property
    def key(self) -> str:
        return self.requirement.key

    @property
    def satisfied(self) -> bool:
        return self.status in _SATISFIED

    @property
    def blocking(self) -> bool:
        return self.requirement.tier == BLOCKING

    def display_value(self) -> str:
        """The value as it is safe to print (secrets redacted)."""
        if self.value is None:
            return "—"
        if not self.requirement.is_secret:
            return self.value
        return (
            credentials.mask_url(self.value)
            if "://" in self.value
            else credentials.mask(self.value)
        )


@dataclass
class PoolResult:
    """Whether one chain has real pool data to work from."""

    chain: str
    status: str
    path: str = ""
    pool_count: int = 0
    detail: str = ""

    @property
    def satisfied(self) -> bool:
        return self.status == OK


@dataclass
class BuildResult:
    """Toolchains + built artifacts. The app fixes these itself (`install`);
    they are reported but never prompted for — you cannot type a Rust
    toolchain into a text box."""

    engine_built: bool
    dashboard_built: bool
    ingestion_built: bool
    engine_python: str | None
    tools: dict = field(default_factory=dict)

    @property
    def ready(self) -> bool:
        return self.engine_built and self.dashboard_built and self.ingestion_built


@dataclass
class HealthReport:
    """The complete picture, and the number the wizard drives to 100%."""

    chains: list[str]
    items: list[ItemResult]
    pools: list[PoolResult]
    build: BuildResult
    probed: bool

    # ── scoring ──────────────────────────────────────────────────────────────
    @property
    def blocking_items(self) -> list[ItemResult]:
        return [i for i in self.items if i.blocking]

    @property
    def total_blocking(self) -> int:
        """Blocking configuration checks: the endpoints you supply, plus one
        pool-data check per selected chain."""
        return len(self.blocking_items) + len(self.pools)

    @property
    def satisfied_blocking(self) -> int:
        return sum(1 for i in self.blocking_items if i.satisfied) + sum(
            1 for p in self.pools if p.satisfied
        )

    @property
    def percent(self) -> float:
        """Configuration health, 0–100. Computed over *blocking* checks only, so
        100% is a real claim: everything the app cannot run without is present,
        valid, and (when probed) actually answering."""
        if self.total_blocking == 0:
            return 0.0
        return 100.0 * self.satisfied_blocking / self.total_blocking

    @property
    def is_complete(self) -> bool:
        return self.total_blocking > 0 and self.satisfied_blocking == self.total_blocking

    @property
    def is_ready(self) -> bool:
        """Fully configured *and* fully built — the app can actually run live."""
        return self.is_complete and self.build.ready

    # ── what still needs doing ───────────────────────────────────────────────
    def unsatisfied(self, *, include_recommended: bool = True) -> list[ItemResult]:
        """Everything still to collect, most important first."""
        tiers = [BLOCKING] + ([RECOMMENDED, OPTIONAL] if include_recommended else [])
        order = {t: n for n, t in enumerate(tiers)}
        pending = [i for i in self.items if not i.satisfied and i.requirement.tier in order]
        return sorted(pending, key=lambda i: order[i.requirement.tier])

    def failing_pools(self) -> list[PoolResult]:
        return [p for p in self.pools if not p.satisfied]


# ── Value resolution ─────────────────────────────────────────────────────────


def resolve(
    req: Requirement, store: credentials.CredentialStore, env: dict[str, str]
) -> tuple[str | None, str]:
    """``(value, source)`` for one requirement, following the documented
    precedence. A whitespace-only environment variable is treated as unset —
    an exported-but-empty var is a very common shell accident and must not
    shadow a real stored value."""
    if req.env_var:
        from_env = (env.get(req.env_var) or "").strip()
        if from_env:
            return from_env, SRC_ENV
    stored = store.get(req.key)
    if stored and stored.strip():
        return stored.strip(), SRC_DB
    if req.default is not None:
        return req.default, SRC_DEFAULT
    return None, SRC_NONE


# ── Network probes ───────────────────────────────────────────────────────────


def probe_chain_id(http_url: str, timeout: float = PROBE_TIMEOUT) -> tuple[int | None, str]:
    """``(chain_id, detail)`` from a real ``eth_chainId`` call.

    Only the first endpoint of a comma-separated failover list is probed — it is
    the one the bot reaches for first, and probing every backup would multiply
    launch latency for little extra signal. Returns ``(None, reason)`` on any
    failure; never raises, because a flaky network must not stop the app
    starting.
    """
    endpoint = http_url.split(",")[0].strip()
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []}).encode()
    request = urllib.request.Request(
        endpoint,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "l2arb-launcher/health"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:  # noqa: S310 - operator-supplied endpoint
            body = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        hint = " (check the API key in the URL)" if exc.code in (401, 403) else ""
        return None, f"HTTP {exc.code} {exc.reason}{hint}"
    except (urllib.error.URLError, OSError) as exc:
        return None, f"could not connect: {getattr(exc, 'reason', exc)}"
    except (ValueError, TypeError):
        return None, "endpoint did not return JSON — is it really an RPC URL?"

    if isinstance(body, dict) and "error" in body:
        return None, f"RPC error: {body['error']}"
    raw = body.get("result") if isinstance(body, dict) else None
    if not isinstance(raw, str):
        return None, "RPC reply had no result"
    try:
        return int(raw, 16), ""
    except ValueError:
        return None, f"unparseable chain id {raw!r}"


def probe_ws_reachable(ws_url: str, timeout: float = PROBE_TIMEOUT) -> tuple[bool, str]:
    """Whether the WebSocket endpoint's host accepts a TLS connection.

    Deliberately **not** a WebSocket handshake: the standard library has no WS
    client, and vendoring one to prove liveness at launch would be a lot of
    surface for a small gain. This opens a real TCP+TLS connection to the host
    and port the bot will use, which is enough to catch a typo'd host, a dead
    DNS name, or a blocked port — the failures that actually happen. It is
    reported in exactly those words so it is never mistaken for proof that the
    subscription itself works.
    """
    endpoint = ws_url.split(",")[0].strip()
    parsed = urllib.parse.urlparse(endpoint)
    if not parsed.hostname:
        return False, "no host in URL"
    secure = parsed.scheme == "wss"
    port = parsed.port or (443 if secure else 80)
    try:
        with socket.create_connection((parsed.hostname, port), timeout=timeout) as sock:
            if secure:
                ctx = ssl.create_default_context()
                with ctx.wrap_socket(sock, server_hostname=parsed.hostname):
                    return True, "host reachable (TLS)"
            return True, "host reachable"
    except ssl.SSLError as exc:
        return False, f"TLS handshake failed: {exc}"
    except (OSError, socket.gaierror) as exc:
        return False, f"could not connect: {exc}"


# ── Pool registry checks ─────────────────────────────────────────────────────


def _count_pools(path) -> int:
    """Number of ``[[pool]]`` entries in a registry file, 0 if unreadable."""
    try:
        return textio.read_text(path).count("[[pool]]")
    except (OSError, UnicodeDecodeError):
        return 0


def check_pools(lo: Layout, chains: list[str], *, repair: bool = True) -> list[PoolResult]:
    """Verify every selected chain has real pool data, materialising the shipped
    registry when it is missing.

    Pool addresses are on-chain facts, never something to type (see
    :mod:`l2arb.requirements`), so the honest handling of "missing" is to fetch
    the verified shipped copy — not to prompt. A chain with no shipped registry
    is reported as blocking with the exact command that fixes it.
    """
    from . import setup as setup_mod

    results: list[PoolResult] = []
    for chain in chains:
        target = lo.state_dir / "pools" / f"{chain}.toml"
        if repair and not target.exists():
            shipped = lo.ingestion / "config" / "pools" / f"{chain}.example.toml"
            if shipped.exists():
                setup_mod.materialize_pool_registries(lo)

        if not target.exists():
            results.append(
                PoolResult(
                    chain,
                    MISSING,
                    detail=(
                        "no pool registry — discover real pools with: python3 "
                        f"ingestion/scripts/discover_pools.py --chain {chain} --http-url <RPC URL>"
                    ),
                )
            )
            continue

        count = _count_pools(target)
        if count == 0:
            results.append(
                PoolResult(
                    chain,
                    INVALID,
                    path=str(target),
                    detail=f"{target} has no [[pool]] entries — it is empty or corrupt",
                )
            )
            continue
        results.append(PoolResult(chain, OK, path=str(target), pool_count=count))
    return results


# ── The check itself ─────────────────────────────────────────────────────────


def selected_chains(store: credentials.CredentialStore) -> list[str]:
    """Chains the operator chose to run, in canonical order.

    Defaults to Arbitrum alone on a fresh install — the quick-start hero path,
    one endpoint to supply and the deepest shipped pool registry. Unknown names
    from a hand-edited database are dropped rather than raising.
    """
    raw = store.get(requirements.SELECTED_CHAINS_KEY) or ""
    picked = {c.strip().lower() for c in raw.split(",") if c.strip()}
    ordered = [c for c in requirements.CHAIN_ORDER if c in picked]
    return ordered or ["arbitrum"]


def env_overrides(
    store: credentials.CredentialStore, chains: list[str] | None = None
) -> dict[str, str]:
    """Stored credentials as environment variables, for the child services.

    The engine and dashboard read their configuration from the environment
    (``L2ARB__*``, ``PORT``, ``PROFIT_RECEIVER``), so whatever the guided setup
    collected has to be handed to them at spawn time — otherwise a value the
    health check calls satisfied would never actually reach the process that
    needs it, which is precisely the dead-config-surface defect root
    ``CLAUDE.md`` §8 item 2 had to remove once already.

    A variable already set in the real environment is *not* included, so the
    operator's own export keeps winning — the same precedence
    :func:`resolve` applies.
    """
    picked = chains if chains is not None else selected_chains(store)
    out: dict[str, str] = {}
    for req in requirements.catalog(picked):
        if not req.env_var or os.environ.get(req.env_var, "").strip():
            continue
        value = store.get(req.key)
        if value and value.strip():
            out[req.env_var] = value.strip()
    return out


def _resolve_and_validate(
    req: Requirement, store: credentials.CredentialStore, env: dict[str, str]
) -> ItemResult:
    """Phase 1: work out the value and check its shape. No network, no threads.

    Kept strictly separate from :func:`_probe` because the credential database
    is a SQLite connection, and SQLite objects may only be used on the thread
    that created them. Resolution is cheap, so it happens here on the calling
    thread; only the slow, connection-free probing is parallelised.
    """
    value, source = resolve(req, store, env)
    if value is None:
        return ItemResult(req, MISSING, source=SRC_NONE)

    error = req.validate(value)
    if error:
        return ItemResult(req, INVALID, value=value, source=source, detail=error)
    return ItemResult(req, OK, value=value, source=source)


def _probe(item: ItemResult) -> ItemResult:
    """Phase 2: prove an endpoint really answers. Network only — safe to thread."""
    req, value, source = item.requirement, item.value, item.source
    if not item.satisfied or value is None:
        return item

    if req.category == requirements.CAT_RPC and req.chain:
        found, detail = probe_chain_id(value)
        if found is None:
            return ItemResult(req, UNREACHABLE, value=value, source=source, detail=detail)
        expected = requirements.CHAINS[req.chain].chain_id
        if found != expected:
            other = next(
                (c.display for c in requirements.CHAINS.values() if c.chain_id == found), None
            )
            named = f" — that endpoint is {other}" if other else ""
            return ItemResult(
                req,
                WRONG_CHAIN,
                value=value,
                source=source,
                detail=f"answered chain id {found}, expected {expected}{named}",
            )
        return ItemResult(req, OK, value=value, source=source, detail=f"live, chain id {found}")

    if req.category == requirements.CAT_WS:
        reachable, detail = probe_ws_reachable(value)
        if not reachable:
            return ItemResult(req, UNREACHABLE, value=value, source=source, detail=detail)
        return ItemResult(req, OK, value=value, source=source, detail=detail)

    return item


def check_build(lo: Layout) -> BuildResult:
    ready = state.probe(lo)
    return BuildResult(
        engine_built=ready.engine,
        dashboard_built=ready.dashboard,
        ingestion_built=ready.ingestion,
        engine_python=prereqs.find_engine_python(),
        tools=prereqs.detect_all(),
    )


def run(
    lo: Layout,
    store: credentials.CredentialStore,
    *,
    env: dict[str, str] | None = None,
    probe: bool = True,
    chains: list[str] | None = None,
    check_build_state: bool = True,
) -> HealthReport:
    """Evaluate the whole install and return a scored report.

    Endpoint probes run concurrently — five chains checked serially at a 6s
    timeout each would add half a minute to every launch, which is exactly the
    kind of tax that makes people disable a health check.
    """
    env = dict(os.environ) if env is None else env
    picked = chains if chains is not None else selected_chains(store)
    catalog = requirements.catalog(picked)

    items = [_resolve_and_validate(req, store, env) for req in catalog]
    if probe:
        with ThreadPoolExecutor(max_workers=8) as pool:
            items = list(pool.map(_probe, items))

    build = (
        check_build(lo)
        if check_build_state
        else BuildResult(False, False, False, None, {})
    )
    return HealthReport(
        chains=picked,
        items=items,
        pools=check_pools(lo, picked),
        build=build,
        probed=probe,
    )
