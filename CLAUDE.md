# CLAUDE.md — operating guide for the **D** super-repo

> **D** is the unified monorepo that merges four previously-separate components
> into one **L2 arbitrage flash-loan bot**: a Rust ingestion layer, a Python
> detection engine, Solidity execution contracts, and a Node/React dashboard —
> wired together, plus a single-entry launcher and a self-bootstrapping Windows
> `.exe`. Read this file first. It is the highest authority on *how* to work in
> this repo; each component keeps its own `CLAUDE.md`/`AGENTS.md` for component-
> specific rules.

---

## 1. What this is

A cohesive product assembled from four components (each still buildable and
testable on its own):

| Dir | Was | Language | Role |
|-----|-----|----------|------|
| `engine/` | Python-Engine-L2-s (`l2arb`) | Python 3.11–3.12 | **Detection** engine — watches L2 DEX state, emits ranked arbitrage opportunities. Detection only. |
| `ingestion/` | L2_bots (`l2-ingest`) | Rust 1.94 | **Data feed** — reads 5 L2s (Arbitrum, Base, Optimism, Unichain, Ink), POSTs state to the engine, fans ranked opps out over WebSocket. |
| `contracts/` | L2_on-chain | Solidity 0.8.20 | **Execution target** — atomic flash-loan arbitrage executor (profit-or-revert). The gated, human-authorised endpoint. |
| `dashboard/` | L2-GUI | Node + React | **UI + API** — live opportunities, wired settings, MetaMask, paper/live executor split. |
| `launcher/` | *(new)* | Python (stdlib) | **Orchestrator** — installs, wires, runs, and supervises the whole stack; payload of the `.exe`. |
| `scripts/`, `docs/`, `.github/` | *(new)* | — | `.exe` build, docs, CI. |

The end-to-end data flow (see `docs/ARCHITECTURE.md` for detail):

```
ingestion ──POST /detect──▶ engine ──ranked opps──▶ ingestion ──ws :9001──▶
  dashboard backend (ExternalProvider) ──REST + /ws──▶ dashboard UI
                                                          │ POST /api/execute/:id
                                                          ▼  (paper by default)
                                                       contracts (gated, human-only)
```

---

## 2. Safety invariants (inherited from every component — binding)

These come from the four component constitutions and **override any impulse to
move faster**. Breaking one is a correctness/safety regression.

1. **Only real, on-chain-verifiable data in runtime paths.** No synthetic,
   random, hard-coded, or "example" market data anywhere a shipped artifact can
   reach it. Synthetic data lives only in clearly-marked tests. Every quote is
   block-stamped and traceable. (engine §3, ingestion prime-directive 1.)
2. **The engine is a detector, not a trader.** It holds no keys, signs nothing,
   submits no transactions. No MEV *extraction* (sandwiching/front-running).
3. **Execution is gated and paper-by-default.** The dashboard ships
   `EXECUTION_MODE=paper` + `autoExecute=false`; `LiveExecutor` refuses to
   broadcast. The safe contract-integration pattern is **simulate via
   `staticCall`, then hand an unsigned tx to a human-authorised signer** — the
   loop never broadcasts and never deploys. (contracts golden rules 2, 5.)
4. **Never fake a test green** — no deleting, skipping, `xfail`, or loosening an
   assertion to pass. A test that legitimately can't run is recorded as BLOCKED
   with a concrete reason, never faked. (ingestion prime-directive 3.)
5. **Leave the tree green.** Every commit passes the touched component's gate
   (below). Never commit secrets or `.env`.
6. **No secrets in code or git.** Endpoints/keys come from the environment or
   config files; only `*.example` is tracked.
7. **`verified` honesty.** Never emit stale/unproven state as `verified:true`.

If a task appears to cross lines 1–3 (e.g. "make the loop send a live trade",
"seed the dashboard with fake opportunities"), **stop, do not implement it**, and
record the concern rather than faking it.

---

## 3. The wiring (what "merged" means concretely)

The components already spoke a common contract; the merge makes them one product:

- **Seam A — ingestion → engine (pre-existing):** `l2-ingest` POSTs a
  `DetectRequest` to `http://127.0.0.1:8080/detect`; the engine returns ranked
  `DetectResponse`. Configured in `ingestion` `[engine]`.
- **Seam B — ingestion → dashboard (built here):** `l2-ingest` broadcasts a
  versioned `Envelope { kind:"opportunities", payload: DetectResponse }` as NDJSON
  WebSocket frames on `:9001`. A new **`ExternalProvider`**
  (`dashboard/backend/src/arbitrage/providers/external.ts`) consumes it, maps each
  engine opportunity onto the dashboard's `ArbitrageOpportunity` shape
  (`providers/engineMap.ts`), and feeds the engine's pull-based scan loop. Select
  it with `DATA_SOURCE=external` + `INGEST_FEED_URL`.
- **Seam C — dashboard → contracts (gated):** `POST /api/execute/:id` runs the
  paper executor by default. Live execution targets
  `contracts/FlashLoanArbitrage.sol::executeArbitrage(ArbParams)` and stays
  human-authorised (see invariant 3).
- **Orchestration:** `launcher/` starts engine + ingestion + dashboard, serves the
  UI single-origin (`SERVE_STATIC_DIR`), health-gates startup, opens the browser,
  and then runs a continuous **health monitor** (a live HUD, `launcher/l2arb/health.py`)
  that probes each service's process + `/health`, self-diagnoses faults, and
  self-heals by restarting a crashed/wedged process with backoff and a bounded
  restart budget — infra recovery only, never touching the human-gated execution
  path. `docker-compose.yml` is the container alternative.

The mapper is **honest about units**: engine amounts are numeraire base units;
they read as USD only when the numeraire is a stablecoin — no ETH price is
fabricated (`engineMap.ts` docstring). `profitBps`/`confidence`/route are always
exact.

- **Latency health (cross-cutting):** a per-batch latency trace rides the pipeline
  — the engine returns a `timing` block on `/detect`, the ingestion envelope adds a
  `latency` field with a single-host wall-clock `origin_wall_ms` anchor, and the
  dashboard aggregates rolling per-stage stats (2–5 per component) + the end-to-end
  at `GET /api/latency`/`/ws` and renders the *Pipeline Latency* HUD. A **separate**
  read-only probe times on-chain execution readiness at `GET /api/health/execution`
  — RPC + optional `staticCall`, **never** a signer/broadcast (invariant 3). Latency
  is measured elapsed time, never fabricated; end-to-end is labelled single-host
  wall clock. Design: `docs/LATENCY.md`.

- **Configuration (cross-cutting):** all env-based config lives in **one master
  `.env` at the repo root** (`.env.example` is the tracked template). The engine
  (`L2ARB__` prefix, via `config._discover_env_files`), the dashboard backend +
  Vite frontend (`dashboard/backend/src/config/env.ts`, `frontend/vite.config.ts`
  `envDir`), and the contracts deploy tooling (`contracts/hardhat.config.js`) all
  read it automatically. Precedence: real env vars → component-local `.env`
  (optional override) → the master → built-in defaults. The Rust ingestion layer
  is the exception — it is TOML-configured (`config.toml`), not env-driven. The
  Blockscout verification oracle takes **only** an API key
  (`L2ARB__BLOCKSCOUT__API_KEY`); its per-chain endpoints are built into the engine
  (`config.BLOCKSCOUT_REST_BASES`). Deploy secrets (`contracts` `PRIVATE_KEY`) are
  fenced in the master and read only by the human-gated deploy step, never the
  running detection stack.

---

## 4. Git & branch discipline

- **Working branch: `claude/l2-arbitrage-flash-loan-bot-embzav`** (this repo, `D`).
- One logical change → one clear commit. Never commit a red tree, secrets, or
  build artifacts (`.gitignore` covers venvs, `node_modules`, `target`, `dist`,
  `*.exe`, `.l2arb/`).
- Component sources were vendored via `git archive` (clean trees, no nested
  `.git`). Only the **root** `.github/workflows` runs as CI; nested component
  workflows are inert history.

---

## 5. Build, test, run

**The whole thing (recommended):**
```bash
cd launcher
python3 -m l2arb doctor      # check toolchains + install state (+ next-step guidance)
python3 -m l2arb install     # build engine venv + dashboard + ingestion binary
python3 -m l2arb run         # paper mode, opens the dashboard at :8787
python3 -m l2arb setup       # guided live setup — paste one Arbitrum RPC URL
python3 -m l2arb run --live  # full stack (setup writes a live-ready .l2arb/config.toml)
```

**Per-component gates (must stay green when you touch that component):**
```bash
# engine
cd engine && make check
# ingestion
cd ingestion && cargo fmt --all --check && cargo clippy --all-targets --all-features -- -D warnings && cargo test --workspace
# dashboard
cd dashboard && pnpm install && pnpm verify          # typecheck + test + build
# contracts
cd contracts && bash scripts/verify.sh               # fmt + build + test (needs forge)
# launcher
python3 -m unittest discover -s launcher/tests
```

**Cross-component integration smoke** (all four seams, on live data):
```bash
cd engine && uv run python ../scripts/e2e_smoke.py
```
Reads real Arbitrum pool state → engine `/detect` → WS envelope → dashboard
`ExternalProvider` → REST/UI controls → paper execute, and asserts the
`LiveExecutor` refuses to broadcast. Skips cleanly (exit 0) when the dashboard
isn't built or there's no outbound Arbitrum RPC, so it's safe offline.

**The `.exe`:** on Windows, double-click **`Build_L2ArbBot.bat`** (repo root) — the
easy-to-find entry point that wraps `scripts/build_windows_exe.ps1`. Also buildable
via `python scripts/build_exe.py` (any OS, for testing), directly via
`scripts/build_windows_exe.ps1`, or the `build-windows-exe` CI workflow, all
producing the real `L2ArbBot.exe`. See `docs/INSTALL.md`.

**Container path:** `docker compose up --build` (see `docker-compose.yml`).

---

## 6. Documentation discipline

Keep docs truthful in the **same commit** as the behaviour they describe. When
you change wiring, ports, or commands, update `README.md`, `docs/ARCHITECTURE.md`,
`docs/INSTALL.md`, and this file. Stale docs are treated as bugs. Each
component's own docs (`*/docs`, `*/CLAUDE.md`, `*/README.md`) remain the
authority for that component's internals.

---

## 7. Scope reminder

In scope: **detecting** 2-hop / triangular / bounded multi-hop / cross-chain
2-hop arbitrage from live on-chain data, surfacing it in a dashboard, and
**simulating** execution against audited flash-loan contracts. Out of scope
(never built here): signing/broadcasting live transactions from the loop,
deploying contracts from the loop, holding private keys, and MEV extraction.

---

## 8. Production debugging + enhancement pass (2026-07-29)

A full-stack audit (`claude/prod-debug-enhancements-x4gzp7`) found the baseline
already green across all five component gates (zero errors) but surfaced real
gaps between what was *documented* as live/enforced and what the code actually
did. Fixed, all covered by new tests, every gate green:

1. **Engine — data-integrity gating was computed but never enforced.**
   `Opportunity.verified` and pool freshness were both tracked but no code path
   ever rejected an unverified/stale pool before pricing it — contradicting
   `docs/DATA_INTEGRITY.md`'s own claim that this was already enforced.
   `detect/profit.py::evaluate()` and `detect/cross_chain.py::_build_opportunity()`
   now gate on both, first (before any AMM math). Freshness is opt-in at the
   pure-compute layer (`ProfitContext.now_ts`/`max_pool_age_seconds`, both
   `None` by default so the gate stays deterministic/testable) but always-on at
   the API boundary (`api/service.py::detect()` resolves a real `now_ts`
   default and the new `L2ARB__MAX_POOL_AGE_SECONDS` operator default, 120s).
2. **Engine — the entire `L2ARB__*` env-config surface was dead code.**
   `get_settings()` had zero call sites outside its own tests. `ChainConfig`/
   `DetectRequest` fields now use `Field(default_factory=lambda:
   get_settings().X)` so an *omitted* request field genuinely falls back to
   the operator's env default (an explicit request value still always wins —
   pydantic only invokes `default_factory` when the field is absent). Also
   wired `L2ARB__LOG_LEVEL` to real `structlog`/stdlib logging
   (`l2arb/logging.py`, new) and fixed the numba JIT cold-start SLO gap by
   calling `graph/tropical.py::warmup()` from the FastAPI lifespan.
3. **Ingestion — L1 data fee had no phantom-profit gate.** The aggregator held
   a chain back when `gas_price_wei == 0` (a fabricated-looking sentinel) but
   had no equivalent for a not-yet-read `l1_data_fee_wei` on the four OP-Stack
   chains. Extracted the combined check into `pipeline.rs::hold_back_reason()`
   (now unit-tested) — gated to OP-Stack only, since Arbitrum's L1 fee is
   legitimately always 0. Also bound `[observability].health_bind` (:9090) on
   its own listener — previously parsed and printed by `--check-config` but
   never actually bound; `/health`+`/metrics` were silently both living on
   `metrics_bind` (:9100) only.
4. **Dashboard — settings enforcement wasn't uniform across providers/paths.**
   `ExternalProvider` (the real production data source) ignored
   `networks`/`dexes`/`tokens`/`baseToken` entirely — only `SimulatedProvider`
   respected them. Moved that filtering into `ArbitrageEngine.qualifies()`
   (the one funnel every provider's candidates pass through) and made
   `maxConcurrentTrades`/`cooldownMs`/`maxDailyLossUsd` (previously enforced
   only inside the `autoExecute()` loop) also gate a manual/API
   `executeOpportunity()` call via a new `riskLimitBlock()` check.
5. **Dashboard — every setting reverted to schema defaults on restart.**
   `settings/persistence.ts` (new) loads/saves `backend/.data/settings.json`
   (git-ignored); `server.ts` wires load-on-boot + save-on-change.
   `executionMode` is the deliberate exception — it always re-seeds from the
   operator's current `EXECUTION_MODE` rather than resuming a possibly-stale
   persisted value (invariant 3 above stays boot-time authoritative).
6. **Launcher — a startup crash or Ctrl-C could orphan already-started
   services.** `run.py`'s sequential engine→ingestion→dashboard startup had no
   `try`/`except` — a mid-sequence failure (or `KeyboardInterrupt`, a
   `BaseException`, while `wait_http` was polling) propagated straight out,
   leaving already-started processes running and holding their ports for the
   next `l2arb run`. Now wrapped so every started service is stopped before
   the error propagates.
7. **Contracts — `DexType.GENERIC` had no router allowlist.** Unlike the typed
   dex types (fixed selector, output recipient hardcoded to `address(this)`),
   `GENERIC` handed `step.router` fully attacker-controlled calldata — a
   compromised `EXECUTOR_ROLE` key could route a step to e.g.
   `transfer(attacker, hugeAmount)` on any token the contract holds, entirely
   bypassing the route-asset and profit checks (which only look at the
   declared first/last leg and the overall balance delta). Added
   `allowedGenericRouters` (deny-all default) + `setGenericRouterAllowed`,
   gated `GUARDIAN_ROLE` (not `EXECUTOR_ROLE` — the hot bot key must not be
   able to expand what `GENERIC` can call). See `contracts/README.md` §Security
   model and `contracts/docs/INTEGRATION.md`.

**Net effect on "is every setting live-adjustable and operational?"**: yes,
with the boot-time exceptions that are deliberate safety invariants
(`EXECUTION_MODE`, `L2ARB__*` env vars — process-level, not hot-reloaded
without a restart, same as before). Full research notes:
`docs/notes-prod-debug-enhancements.md`.

---

## 9. Granular stress-test + enhancement audit (2026-07-31)

A second, deeper full-stack audit (`claude/stress-test-audit-pm9eya`) re-verified
the baseline **green across every runnable gate** (engine 440, ingestion 185,
dashboard 76+28, contracts-Hardhat 37, launcher 76 — the Foundry suite is BLOCKED
here, `forge` not installed, recorded not faked) and then hunted, with five parallel
per-component auditors, for defects between documented and actual behaviour. Every
finding was independently re-verified before any change. **18 confirmed defects
fixed, each with a regression test where testable; all gates re-run green.** The
lower-severity remainder is recorded (not faked) as a triaged, reproducible backlog
in `docs/notes-stress-test-audit.md`.

The fixes, most-severe first:

1. **Dashboard — the entire live data path was dark (CRITICAL).**
   `ArbitrageEngine.qualifies()` filtered each route leg's `dex` against the
   venue-key chip set, but `ExternalProvider` (the real `DATA_SOURCE=external` feed
   that `l2arb run --live` uses) labels a leg with its *pool address* — engine data
   carries no venue brand. So **every** external opportunity was silently dropped and
   the live dashboard was permanently empty. No test composed `engineMap → qualifies()`,
   which is why it hid. The venue chip now applies only to legs labelled with a venue
   key we model (`KNOWN_VENUE_KEYS`); an unlabelled pool-address leg keeps its
   network/token/profit filters but is not venue-filtered (fabricating a venue would
   break invariant 2.1). Also: USD-magnitude gates apply only to USD-denominated opps
   (`numeraireIsUsd !== false`); `maxDailyLossUsd=0` no longer halts at t0 (strict `<`).
2. **Contracts — held-token drain via a discontinuous route (fund-safety).**
   `_runRoute`'s "spend up to the live balance" cap, combined with no step-to-step
   continuity check, let a compromised `EXECUTOR` name an intermediate `tokenIn` the
   previous hop didn't produce but the contract holds (a parked/airdropped token),
   vacuuming it into the trade and paying it out as "profit" — with plain typed dexes,
   bypassing the GENERIC allowlist. `executeArbitrage` now requires
   `steps[i].tokenIn == steps[i-1].tokenOut` (new `RouteNotContiguous`).
3. **Engine — a degenerate StableSwap crashed the whole `/detect` batch (HIGH).**
   `-math.log(0.0)` on an imbalanced-but-valid StableSwap pool's 0.0 marginal rate
   raised `ValueError` with no downstream `try/except`, losing every opportunity in
   the batch. Such an edge is now skipped like an untradable one. Plus: a cross-chain
   phantom-profit guard when a numeraire/asset has different decimals across chains,
   and the cross-chain verified/freshness gate now runs *before* pricing (matching the
   `DATA_INTEGRITY.md` guarantee).
4. **Launcher — the Windows live path was broken + process-lifecycle leaks (HIGH).**
   `setup` wrote the pool path into a TOML *basic* string, so a Windows backslash path
   became invalid escapes and the whole `config.toml` failed to parse (flagship `.exe`,
   masked by a POSIX-only test) — now TOML-escaped. Plus: SIGKILL-path zombie reaping,
   a SIGTERM handler (so `kill`/`docker stop` doesn't orphan children), restored
   startup-grace on restart, a closed browser-open/monitor-construction orphan window,
   and a Popen-failure fd leak.
5. **Ingestion — L1-fee under-cost + a permanently-dead WS accept loop (HIGH/MED).**
   The L1 data fee was sampled with an *empty* tx (zero calldata → phantom profit on
   OP-Stack); it now samples a real recorded tx (arb-sized/config-driven sample is a
   recorded follow-up). The WS accept loop no longer dies permanently on a transient
   `accept()` error, and the reconnect backoff resets after a stable connection.

**Recorded (not fixed) — see `docs/notes-stress-test-audit.md`:** the standout is an
ingestion bug where `retain_valid` validates the engine response against the
*incremental delta* only, dropping opportunities that route through unchanged pools
(feed near-silent in steady state) — CONFIRMED by trace, but the fix touches a
safety-critical validation path whose end-to-end correctness needs the real `l2arb`
engine, which is BLOCKED in this environment; recorded with a precise recommended fix
rather than shipped speculatively. Also recorded: cross-chain-executor GENERIC
hardening, in-range V3/V4 liquidity events, and assorted lower-severity items.

---

## 10. Dashboard contract deploy/monitor + MetaMask + live stress test (2026-08-01)

Branch `claude/flash-contract-stress-test-l5smfa`. Added an operator-facing
**Contracts** capability to the dashboard — compile, deploy, monitor, and a
read-only live readiness sweep — plus first-class MetaMask SDK wallet operation,
all built to the binding safety model (root §2/§3, contracts golden rule 5):
**the backend never holds a key and never broadcasts; every on-chain write is
signed by the operator's MetaMask.** Every gate re-run green (contracts 40
offline, dashboard `pnpm verify` = typecheck + 102 backend + 34 frontend +
build). Foundry Solidity suite remains BLOCKED (`forge` not installed — recorded,
not faked).

What was built, and the safety reasoning:

1. **Compile / deploy buttons + status monitor (`dashboard/backend/src/contracts/*`,
   `frontend/src/components/ContractsPanel.tsx`).** New `/api/contracts/*` surface:
   `status` (per-chain *verify-provider → compile → deploy → ready* monitor),
   `compile` (server-side Hardhat — no chain, no key), `artifact/:name` (serves the
   compiled ABI+bytecode), `deploy-params/:network` (constructor args from the
   **verified** `config/addresses.js` only — an unverified chain 400s, never
   inventing an address per golden rule 7), `deployment` (records a deploy),
   `readiness` (the sweep). The **deploy** flow is the sanctioned pattern: the
   browser fetches the artifact and **`useDeployContract` signs it in MetaMask**;
   the backend only *records the resulting public address* into
   `contracts/deployments/<net>.json` **and** the master `.env`
   (`FLASH_LOAN_EXECUTOR_ADDRESS_<NET>`, filling the singular probe var/chain only
   if empty). The `.env` writer (`envFile.ts`) refuses any non-address key, so a
   secret can never be written.

2. **Full MetaMask SDK integration (`frontend/src/config/wagmi.ts`,
   `WalletButton.tsx`).** Added the first-class wagmi `metaMask()` connector
   (wraps `@metamask/sdk`; extension + mobile deep-link/QR) alongside the existing
   injected/Coinbase fallbacks; the connect button prefers it. MetaMask *is* the
   "human-authorised signer" the whole product is architected around — this is the
   piece that makes gated live deploy/execute possible without a server-side key.

3. **Profit → connected wallet.** The atomic executor **already** forwards 100% of
   profit to `profitReceiver` (or `msg.sender` when unset) and retains nothing
   (`FlashLoanArbitrage._settle`); added the missing-coverage regression test for
   the `profitReceiver = 0` → tx-signer fallback (the "profit straight to your
   connected wallet by default" path) and surfaced the guarantee in the UI. So #5
   was a *wiring/verification* task, not a contract rewrite.

4. **Live stress test — honestly shaped.** A real profitable arb can't be forced on
   mainnet (profit-or-revert ⇒ a blind fire just reverts), and unattended
   broadcast is the forbidden line. So the "stress test across every chain +
   cross-chain" is delivered as: deploy the real contracts per chain via MetaMask,
   then a **strictly read-only** readiness sweep (`getCode` + `aavePremiumBps()`
   staticCall) over every recorded deployment — exposed both in the UI ("Run
   readiness sweep") and headless (`scripts/contract_stress_test.mjs`, raw
   JSON-RPC, offline-safe/exit-0 like `e2e_smoke.py`). Real execution stays a
   human-signed MetaMask action.

**Deliberately NOT built (crosses binding invariants):** a backend-held hot key,
any server-side broadcast, or an unattended auto-fire loop. **Honest limitation
recorded:** the dashboard's engine-fed opportunities carry detection data (pool
addresses + token symbols), *not* an executable `ArbParams` route (router
addresses, DexType, calldata) — see `engineMap.ts` — so a one-click *live execute*
of an arbitrary opportunity is not constructible without fabricating route data
(invariant 1). Live execution therefore requires the real route params; the panel
delivers deploy + read-only simulation and leaves execution to the human-signed
path. Full notes: `docs/notes-flash-contract-stress-test.md`.

---

## 11. Ingestion production audit + stress test (2026-08-03)

A 4th full audit pass (`claude/ingestion-audit-stress-test-wgbo9y`), focused on
the **ingestion** component per the task brief, plus a full-stack live-readiness
verification. Two environment blockers every prior session recorded as BLOCKED
are **lifted here**: outbound Arbitrum RPC works, and the real `l2arb` engine
stands up locally (`cd engine && uv sync --all-extras && uv run uvicorn
l2arb.api.http:app --port 8080`). That unblocked `scripts/e2e_smoke.py` to run
to completion against real chain state for the first time, and closed out
`crates/engine-client/tests/live_engine.rs` — a test written for exactly this
scenario that had never been able to run before. `forge`/Foundry remains not
installed (BLOCKED, not faked); Hardhat's 40-test suite still covers the
contracts.

Baseline reconfirmed green (engine 442/99.87% cov, ingestion 186, contracts
Hardhat 40, dashboard 102+34, launcher 80 — 884 tests). Audit method: 4 parallel
subagents split the ingestion codebase by crate group, 1 audited every
interactive dashboard control against its backend wiring, 1 re-verified the
contracts profit-to-wallet path and that detected opportunities are real. Every
finding independently re-verified by direct code reading (and a regression test,
for anything fixed) before being recorded. Full ledger:
`docs/notes-ingestion-audit-stress-test.md`.

**7 confirmed defects fixed**, most-severe first:

1. **Ingestion, HIGH — the `retain_valid` root cause, corrected.** Three prior
   sessions recorded a "feed goes near-silent in steady state" issue and
   attributed it to `retain_valid`'s validation logic. With the real engine now
   reachable, the true cause is different and worse: `pipeline.rs` truncated
   `req.pools` to the incremental delta, but the real `l2arb` engine builds a
   **fresh, fully stateless graph on every `/detect` call** — an omitted pool
   isn't "safely already known" to it, it's absent from the search entirely.
   Proven with a live A/B test (partial pool set → 0 opportunities; full set →
   2, correct profit). This made the ordinary case — one leg's pool moves, its
   cycle partner doesn't, same tick — undetectable after a session's first
   tick. Fixed: ingestion now always sends each chain's full current verified
   snapshot; `incremental` stays a wire signal only.
2. **Ingestion, CRITICAL — V3 Mint/Burn and V4 ModifyLiquidity were never
   dispatched.** Fully decoded, fully applied to the mirror, directly
   unit-tested at the decode/mirror layer — but the live log dispatcher
   (`apply_log`) had no branch for either, so both were silently dropped on the
   live path while `re_stamp` kept re-labelling the resulting stale liquidity
   `verified:true` every tick. Reconcile can't catch it (checks each pool at
   its own already-stale block). Fixed: both branches wired; `apply_log`
   refactored to a free function so it's directly unit-testable; 5 new tests.
3. **Dashboard, HIGH (same severity class as #1) — found live, not by static
   analysis.** Running the real `e2e_smoke.py` end-to-end surfaced a genuine
   engine-detected opportunity that never reached `/api/opportunities`.
   `qualifies()` required an exact `opp.tokenIn === s.baseToken` match; the
   engine closes a cycle in whichever hub token actually produced the edge
   (every shipped chain's `hubs` list WETH *and* USDC, several also USDT), not
   the one the operator picked as "Base asset" — so any real opportunity in a
   non-default numeraire was silently dropped, exactly the §9 "entire live
   data path was dark" bug's shape recurring on a different field. A
   *pre-existing test* had encoded the bug as correct behavior; corrected, not
   deleted. Verified live twice: manual repro, then a clean 12/12
   `e2e_smoke.py` run.
4. **Ingestion, HIGH — config validation didn't require any chain be
   enabled.** A config with every `[[chains]]` block present but all disabled
   passed `--check-config` and ran forever idling, `/health` reporting healthy
   throughout. Fixed.
5. Two more small fixes: a purpose-built engine-integration test had a
   hardcoded blockstamp that had drifted ~385 days stale relative to a later
   session's freshness gate — fixed to use real wall-clock time, now passes
   against the real engine for the first time. `e2e_smoke.py`'s own final
   safety-check gave a false-positive "SAFETY REGRESSION" alarm due to a test
   sequencing bug (an unrelated cooldown gate firing before `LiveExecutor` got
   a chance to run) — fixed; confirmed `LiveExecutor` itself was never actually
   broken.
6. **Dashboard, HIGH — the header Play/Pause button showed a stale "Running"
   state indefinitely after a pause** (the one code path that skipped the stats
   push to connected clients). Plus two MEDIUM UX-honesty fixes: executing an
   expired opportunity failed completely silently (now alerts, like every
   other rejection path), and MetaMask connect/network-switch rejections were
   silently swallowed (now surfaced, matching the pattern the Contracts panel
   already used correctly). Plus two LOW one-line fixes (a missing input clamp
   matching the backend schema; `resetSettings()` had no error handling at
   all).

**14 further findings recorded** (not fixed this session) with precise
reproduction steps and recommended fixes — most notably: no liveness/staleness
watchdog on a chain's WS subscription (CRITICAL — `verified:true` can mean
"frozen," live-reproduced, but the correct fix restructures the
supervisor/health startup order across several files, which isn't something to
rush against a safety-relevant path); HTTP endpoint failover never actually
triggers for a genuinely dead endpoint, its primary use case (HIGH,
empirically proven); the L1 data-fee sample is undersized 20–63× for a real
multi-hop route's calldata (HIGH, precisely quantified). Two findings from the
initial audit passes (a reseed/generation-bump race; an `IncrementalTracker`
cache-key collision risk) turned out to be **substantially defused as a side
effect of fix #1** — noted explicitly in the notes file rather than left at
their original severity.

**Contracts + opportunity-detection re-verification: both hold, nothing to
fix.** Re-checked against current HEAD rather than trusting the changelog:
profit routing to the connected wallet, route-contiguity, and the GENERIC
router allowlist are all still enforced and test-covered; an exhaustive grep of
the whole `dashboard/` tree found zero signer construction and zero
autonomous-broadcast path anywhere — deployment remains the only
browser-signed chain-write in the product. The honest limitation from §10 is
unchanged: detected opportunities still can't be turned into a one-click live
execution without fabricating route data the engine doesn't emit.

**Net result:** ingestion 186→194 tests, dashboard 102+34→107+37 tests, both
gates green; `e2e_smoke.py` and `live_engine.rs`'s real-engine test both pass
for the first time in this repo's history.
