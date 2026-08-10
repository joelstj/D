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

---

## 12. Cross-chain flash-loan arbitrage gap audit (2026-08-04)

A 5th audit pass (`claude/cross-chain-flash-loan-gaps-v2fki8`), this time scoped
specifically to **cross-chain** execution: what's actually blocking a successful
cross-chain arbitrage trade, as opposed to the same-chain path prior sessions
already hardened. Framing, confirmed before auditing anything, not assumed:
cross-chain arbitrage in this repo is — correctly, by design —
**non-atomic**. `contracts/contracts/crosschain/CrossChainArbitrageExecutor.sol`
implements the honest, real-world model: two separate transactions
(`executeSourceLeg` on chain A, a bridge, `executeDestinationLeg` on chain B),
never a fictitious "atomic cross-chain flash loan" (a transaction cannot span
two chains). This audit measured the system against that honest premise, not
against a stricter atomicity guarantee that was never claimed.

Five parallel read-only audits (contracts, engine, ingestion, dashboard, and a
repo-wide sweep for the spec-mandated off-chain orchestrator) confirmed the
on-chain leg-execution primitive is real and individually tested, but was
entirely unreachable end-to-end: no real bridge adapter existed (only a test
mock), nothing anywhere called `executeSourceLeg`/`executeDestinationLeg`
outside the contract's own tests, and the engine's profit/risk math hadn't yet
accounted for the two-transaction reality. Full findings, severity, and the
fix/record reasoning for every item: `docs/notes-cross-chain-flash-loan-gaps.md`.
Baseline reconfirmed green across every runnable gate before any change
(contracts 48 prior to this session's additions, engine 460, ingestion full
workspace, dashboard 171 — Foundry remains BLOCKED, `forge` still not
installed in this environment, consistent with every prior session).

**13 confirmed defects fixed**, each with a regression test, all gates re-run
green after every commit (not just at the end):

1. **Contracts — a bridge-adapter drain vector, and no sibling-address
   guardrail.** `executeSourceLeg` `forceApprove`d a caller-supplied
   `bridgeAdapter` for the *entire* held balance with no allowlist — the exact
   same shape of risk as the `DexType.GENERIC` router issue §8 item 7 already
   fixed on `FlashLoanArbitrage.sol`, just never applied here. Added
   `allowedBridgeAdapters` (deny-by-default, `GUARDIAN_ROLE`-gated), mirroring
   that fix precisely. Also added a `siblingExecutor` registry
   (chainId → known-good address) so a registered chain enforces
   `dstRecipient` matches it — additive/backward-compatible, chains with no
   registered sibling behave exactly as before.
2. **Engine — the cross-chain profit gate had zero allowance for price
   movement during settlement.** Both legs were priced off the same instant
   snapshot with no discount for the real wait (`settle_seconds`, often 600s+
   — 5x past the engine's own 120s same-chain freshness bar), and the
   cross-chain risk penalty was a flat constant regardless of that wait, so a
   30-second fast-bridge opportunity and a 60-minute canonical-bridge
   opportunity got an identical confidence score. Added a settle-time-scaled
   `price_drift_cost` (opt-in/`None` at the pure-compute layer, a real
   operator default via `L2ARB__CROSS_CHAIN_PRICE_DRIFT_BPS_PER_MINUTE` at the
   API boundary — same threading pattern as the existing freshness gate) and
   split `MevModel`'s cross-chain penalty into a base risk plus a per-minute
   component, calibrated to reproduce the old flat value exactly at the 600s
   reference wait this codebase's own fixtures already used, so it's a
   refinement, not a re-guess. Also closed a numeraire-fungibility gap: only
   the bridged *asset* was checked via `registry.are_fungible()`; the
   numeraire got a strictly weaker decimals-only check 20 lines away — now
   checks both.
3. **Ingestion — cross-chain could silently resolve to fully inert.**
   `config.example.toml` ships `[cross_chain] enabled = true` with placeholder
   addresses; `--check-config` printed the raw unfiltered config (looking
   fully wired) without ever running the real parse/filter path, and the live
   process would silently send `cross_chain: None` forever with zero log
   signal — the same "healthy-looking total outage" shape §9/§11 already rated
   CRITICAL/HIGH elsewhere in this repo. Now warns when configured-enabled
   cross-chain detection ends up with zero usable assets, and `--check-config`
   reports genuine post-filter counts. Also fixed `validate_response`'s
   `verified_pools` set being keyed on bare address with no `chain_id` (several
   configured L2s are OP-Stack siblings sharing identical predeploy addresses,
   so a pool verified on chain A could rubber-stamp an unverified same-address
   leg on chain B — closed with an adversarial regression test proving the
   collision is actually dropped), and `filter_cross_chain` not enforcing its
   own documented "must have a real bridge" invariant.
4. **Dashboard — cross-chain opportunities couldn't be represented, were
   executed dishonestly, and could bypass network filters.**
   `ArbitrageOpportunity` had exactly one chain field, and the mapper always
   resolved it to the source chain — the destination chain was discarded
   unconditionally, every time. Added `isCrossChain`/`destChainId`/
   `destNetwork`/`settleSeconds`, resolved defensively (never guessed when
   ambiguous). Built on that: `PaperExecutor` had been modelling every revert
   as "atomic, lose only gas," which is false for a contract whose own NatSpec
   says capital sits in flight between two separate transactions — a
   cross-chain opportunity is now never run through the atomic model, coming
   back `status:"skipped"` with an honest reason instead (and excluded from
   executed/reverted stats, so a modelling refusal can't misread as a real
   failed trade). `qualifies()` now also checks the destination network, not
   just the source — previously an operator who explicitly disabled a chain
   had no way to keep a cross-chain trade routed through it from qualifying.
   Finally, the Contracts panel's deploy flow — hardcoded to
   `FlashLoanArbitrage` despite `CrossChainArbitrageExecutor` already being
   scaffolded in the backend — now supports deploying and recording both
   contracts through the same MetaMask-signed flow.

**15 further findings recorded, not fixed this session** — see the notes file
for full per-item reasoning, but the throughline is the same one root
`CLAUDE.md` §2 already states as a binding invariant: a real bridge-protocol
integration, and the off-chain orchestrator the spec (`contracts/docs/specs/
10-cross-chain.md`) assigns exposure caps, hedging, inventory accounting, and
deadline/refund handling to, are Phase-9-scoped and explicitly gated on
human-set risk parameters ("NEEDS HUMAN" in the spec's own words). Building
any of that speculatively this session — guessing at exposure-cap dollar
values or a hedging strategy — would be exactly the kind of thing to stop and
record rather than fake. Most notable recorded items: no real `IBridgeAdapter`
implementation exists anywhere (only a test mock — this is the single biggest
remaining blocker to any live cross-chain trade); no on-chain or off-chain
exposure cap; no deadline/refund path if a bridge stalls after the source leg
fires (recovery is manual, privileged `rescueTokens`, not a protocol
guarantee); the engine's emitted `Opportunity` still carries zero fields an
executor would need to construct a real route (same category as the
already-accepted same-chain limitation from §10, but total rather than
partial for cross-chain); and no unified kill switch pauses both contracts
together (a MetaMask-signed "Pause/Unpause" panel mirroring the existing
deploy-signing pattern is the recommended shape for a future session).

**Net result:** contracts 48 tests (+12), engine 460 tests (full suite,
99.87% coverage), ingestion full workspace green (fmt+clippy clean), dashboard
171 tests (126 backend + 45 frontend) + both builds — all four gates verified
green by this session directly (not just trusted from a sub-agent's report)
before each component's commit. The on-chain execution primitive, the
detection math, the data model, and the execution/filtering wiring are now
individually correct and honest about the non-atomic reality; what remains
before a live cross-chain trade is possible is the real bridge integration and
the off-chain orchestrator — both correctly out of scope for a same-session
fix, both precisely specified in `docs/notes-cross-chain-flash-loan-gaps.md`
for whichever session picks them up.

---

## 13. Production audit + first live flash-loan execution (2026-08-09)

A 6th audit pass (`claude/audit-flash-loan-execute-qkyth2`). Task: *"Run a granular
production grade audit and fill any gaps and try to live execute one flash loan
and deposit it into the environments metamask wallet address."* Full ledger:
`docs/notes-audit-flash-loan-execute.md`.

**Environment change worth knowing about.** This is the first session whose
container carries **real operator credentials**: `EXECUTOR_PRIVATE_KEY` (deriving
to `0x50A71dF7DfC5850e8434C7c8A564366F4980183b`), a matching `PROFIT_RECEIVER`
env var, and working Polygon/Arbitrum/Base RPC. Balances are small (~0.0035 ETH
on Base, ~0.09 POL on Polygon, dust on Arbitrum, 0 on Optimism). `ARB_CONTRACT_
ADDRESS` is also set and holds real bytecode on Polygon, but it belongs to a
**different project** in this workspace — it is not a `FlashLoanArbitrage`
deployment and must not be treated as one. This repo still has **zero deployed
contracts** on any chain.

**The live-execution half, and where the line was drawn.** A mainnet broadcast
was *not* performed, for three independent reasons, any one of which is
sufficient: (1) §2 invariant 3, §10, and `contracts/CLAUDE.md` golden rule 5 all
forbid the loop broadcasting or deploying — §2 says to stop and record rather
than implement it; (2) nothing is deployed to call, and deploying is itself a
forbidden write; (3) profit-or-revert plus the still-open "engine emits no
executable route" limitation (§10, §12-E2) means a blind fire just reverts and
burns gas. The user was asked explicitly which of three paths to take
(fork-execute / deploy-only / full broadcast) and did not answer, so the
non-destructive path was taken and the decision left open.

What *was* delivered is the strongest proof available without crossing that
line — and it is genuinely new capability, not a restatement:

1. **`contracts/scripts/live_flash_loan_fork.js` (new).** Executes **one real
   flash loan** against live chain state: forks the chain at its current head,
   deploys this repo's real executor, borrows from the **real Aave V3 pool at
   the real live premium**, routes through **real Uniswap V3 + real
   QuickSwap/SushiSwap pools**, repays, and deposits the profit to a named
   wallet. Verified on both chains — Polygon: **1621.94 WMATIC profit on a
   3442.05 WMATIC loan** (live 5 bps premium); Arbitrum: **0.0489 WETH on a
   0.0396 WETH loan** — each asserting three things independently (receiver
   balance delta, the `ArbitrageExecuted` log's `profit` *and* `profitReceiver`
   args, and zero residual in the executor). The destination address was
   corroborated from two unrelated sources (the env var and the key derivation).
   **Safety:** `assertForkOnly` refuses every real network in
   `hardhat.config.js`, so the script cannot be repointed at mainnet by changing
   a flag; it builds no signer over the key (address derivation only). That
   guard is unit-tested directly, and both failure paths were verified to exit
   non-zero. Like the fork suites it manufactures its dislocation, and says so
   in its own output — it proves the pipeline works and pays the right wallet,
   **not** that this profit exists on mainnet.

2. **Fork suites unblocked and given CI coverage for the first time.** Both
   Hardhat mainnet-fork suites ran here against live state — the first time
   either has executed in this repo's history (Arbitrum 4 passing, Polygon 5
   passing, cross-chain dual-fork 1 passing). Then the gap that kept them dark:
   nothing ran them. `npm test` pinned four offline files, `test:fork*` were
   hand-invoked, and CI's only *fork* hook pointed at the **Foundry** suite,
   gated on an `ARBITRUM_RPC_URL` secret that has never been configured — so
   that step has never actually run either. So the repo's only end-to-end
   execution proof had zero automated coverage behind a CI step that looked
   like it provided some. All three Hardhat suites plus the live-execution
   script are now wired into CI, each gated on the RPC secret it needs.

   **Correction to the earlier framing in this file's history:** Foundry is
   unavailable in these *sandboxed sessions* (`forge` not installed —
   reconfirmed again here, so the local Solidity suite stays BLOCKED, not
   faked), but it installs and runs fine **in CI** via `foundry-toolchain@v1`:
   the run on this PR executed 16 Foundry tests green. Only the fork-gated
   Foundry step is dark, and only for want of a secret. Prior sessions' "the
   Foundry suite has never executed" should be read as "never executed *in a
   dev sandbox*" — it is not true of CI.

**3 confirmed defects fixed**, each with a regression test:

1. **Contracts, MEDIUM — profit was paid to the right wallet but logged to
   `0x0`.** `_settle` resolved `profitReceiver == address(0)` to the tx signer
   when transferring, then emitted the **raw** `p.profitReceiver` in
   `ArbitrageExecuted`. That default path is exactly the "profit straight to
   your connected wallet" behaviour §10 item 3 advertises, and this engine keeps
   its PnL history in logs rather than storage (per the event's own docstring),
   with the receiver field `indexed` — so a downstream indexer filtering
   "arbitrage that paid me" by topic matched **nothing** for the most commonly
   used path. The pre-existing regression test asserted balances only and never
   inspected the event, which is why it hid — the same "no test composed the two
   layers" shape as §11 item 3. The new test was run against the pre-fix
   contract and confirmed to fail before being accepted.
2. **Contracts, MEDIUM — asymmetric-fee pairs were systematically mis-sized.**
   `quoteOptimalTwoHopV2` takes a fee for *each* pool and documents both as
   such, but forwarded only `feeBpsBuy` into `optimalV2Amount`, whose own single
   `feeBps` is documented as "applied on both hops" — while the profit estimate
   returned alongside already charged each pool its own fee, so the two halves
   of the quote disagreed. Any asymmetric pairing (a 0.30% V2 pool against a
   0.05% V3-style pool — the common real case) was sized for a pair that does
   not exist. Generalised the closed form to keep both multipliers; it collapses
   to the shipped formula exactly when the fees match, so it extends the
   existing math rather than replacing it. Validated numerically against a
   brute-forced optimum *before* writing Solidity: exact parity at equal fees,
   and **+0.01% to +3.06%** more profit across asymmetric pairs, with the gain
   largest where the spread is tightest.
3. **CI, MEDIUM** — the fork-coverage gap described above.

**1 finding recorded, deliberately not "fixed":** no operator-facing surface in
`dashboard/`, `engine/` or `launcher/` sets `profitReceiver` (grep confirms the
identifier lives only in `contracts/` and its own tests/examples). This looks
like a wiring gap on the task's exact ask, but adding a setting there would be
**dead config** — the dashboard has no live-execution path at all by design, and
dead env surface is precisely the defect class §8 item 2 already had to remove.
The code that actually builds `ArbParams` — `contracts/integration/examples/
bot.js:59` and `bot.py:52` — already sets `profitReceiver` to the signer's own
address, correctly. Recorded so a future session doesn't "fix" it into dead code.

**Re-verified, no defect:** callback caller/initiator validation, the
`_CB_ARMED` latch, route contiguity, the GENERIC router allowlist, pre-balance
snapshotting, and the `_runRoute` live-balance cap all still hold at HEAD. The
Yul hot paths (`_swapUniswapV2`, `_swapUniswapV3Single`, `balanceOf`,
`_getReserves`, `aavePremiumBps`) were re-derived by hand against their
documented calldata layouts — offsets and selector sourcing correct.

**Net result:** contracts **48 → 66** offline tests plus 10 live-fork tests now
running and CI-wired; engine 460 / ingestion 204 / dashboard 126+45 / launcher
80 all re-run green and untouched. **What still blocks a real mainnet flash
loan is unchanged and is not a code bug in the executor:** no executable route
data from the engine (§10, §12-E2), no deployment on any chain, and a thin gas
buffer. The executor itself is proven operational against live infrastructure
on two chains and pays the wallet it is told to.

---

## 14. First real mainnet deployment (2026-08-10)

Follow-up to §13, same session. After PR #25 merged, the operator was asked once more whether
to proceed with a real broadcast — the concern having already been raised twice (an unanswered
`AskUserQuestion`, then an explicit closing ask). They replied **"Go ahead."** A concern raised
and then reaffirmed is the user's decision, so the deployment was performed.

**This is a deliberate, human-authorised override of §2 invariant 3 and `contracts/CLAUDE.md`
golden rule 5** ("never deploy or send a live transaction from the loop"). It is recorded here
rather than quietly done. The invariants themselves are unchanged and still bind future
sessions: a future agent must not read this as standing permission to broadcast. Each such
action needs its own explicit human authorisation.

**`FlashLoanArbitrage` is live on Base at `0x17fB2Da9D6b6f95962Ad21f39aAE43f40Caaf602`**
(admin/guardian/executor = `0x50A71dF7DfC5850e8434C7c8A564366F4980183b`, aavePool
`0xA238Dd80…d1c5`, balancerVault `0xBA122222…F2C8`). Actual cost **0.0000188 ETH (~$0.07)**.
Base was the only viable chain: Arbitrum holds 0.000002 ETH, and Polygon's 0.09 POL is roughly
one deploy at 30 gwei with no headroom. `CrossChainArbitrageExecutor` was skipped
(`SKIP_CROSSCHAIN=1`) — inert without a sibling on a second chain, and there is no gas for one.

**What was NOT done:** `executeArbitrage` was not broadcast. With no engine-emitted route data
and profit-or-revert semantics, a blind fire is a guaranteed revert and pure gas burn. That was
stated to the operator and was not part of what they approved.

Two new scripts, both of which earn their place:

1. **`contracts/scripts/preflight_deploy.js`** — read-only. Refuses to proceed if either
   flash-loan provider address has no code on the target chain, then reports a real
   `eth_estimateGas` against live state and whether the deployer can afford it with headroom.
   Run this before `deploy.js` on any real network; it issues no writes.
2. **`contracts/scripts/verify_deployment_executes.js`** — forks the chain at its current head so
   the *deployed* contract exists in fork state, then drives **that address's real on-chain
   bytecode** (not a fresh compile) through a full arbitrage. This is the difference between "the
   code we compiled works" and "the thing actually sitting at that address works." Result on Base:
   borrowed 50 WETH from the real Aave V3 pool at the real 5 bps premium, **profit 0.1033 WETH**
   to the operator wallet, gas 495,341, logged profit == balance delta, executor residual 0.

**Verified independently on-chain**, not trusting the deploy script's own output: 13,660 bytes of
bytecode; `aavePremiumBps()` staticCall returns **5**, read live from the real Aave pool (proving
both the ABI and the Aave wiring); `paused()` false; `hasRole(EXECUTOR_ROLE, deployer)` true; and
the repo's own `scripts/contract_stress_test.mjs` readiness sweep passes (1 chain, 0 failures).

**Two gotchas worth keeping** (both found empirically here, both cost real debugging time):

- **`BAL#528`** — Balancer's Base vault holds only ~27.9 WETH, so a 50 WETH flash loan reverts
  `INSUFFICIENT_FLASH_LOAN_BALANCE`. Aave's Base reserve holds ~18,000 WETH. Check provider
  liquidity before sizing, per chain — it differs wildly between the two providers.
- **Hardfork history on a pinned fork** — an `eth_call` at *exactly* the fork block is treated as
  historical execution and fails with "No known hardfork … in chain with id 8453" despite
  `hardhat.config.js` declaring `8453: { hardforkHistory: { cancun: 0 } }`. Mine one block past the
  fork point (`hardhat_mine`) so execution lands on a locally-mined block. Separately, the public
  `mainnet.base.org` is load-balanced and can serve a stale head, so an unpinned fork may land
  *before* a just-sent deployment — pin `FORK_BLOCK`.

**Net effect on "can this bot make a real trade?"** One blocker cleared, two standing. There is now
a real, live, role-configured executor on Base, proven to run. Still missing: the engine emits
detection data, not constructible `ArbParams` (§10, §12-E2), so nothing in this repo can produce a
real profitable route today; and the gas buffer is ~0.0034 ETH. The 0.1033 WETH above came from a
manufactured dislocation on a fork — **not** realised profit. The operator's real balance is
unchanged apart from the deploy gas.

---

## 15. Cross-chain arbitrage stress test + live execution proof (2026-08-10)

A 7th audit pass (`claude/cross-chain-arbitrage-stress-test-psxjbh`), the 2nd focused specifically on
cross-chain (after §12). Task: *"Run a comprehensive stress test and report any gaps or problems we
have that need to be filled and try to execute one cross chain arbitrage flash loan smart contract
successfully."* Baseline reconfirmed green across every gate **independently, by this session directly
— before and after every fix**, not just trusted from a sub-agent's report (contracts 66, engine 460,
ingestion 204, dashboard 171, launcher 80 — 884 tests). Method: four parallel fresh-eyes audits
(engine, ingestion, dashboard, launcher), each briefed with the exact findings of every prior session
so they hunt for *new* gaps instead of rediscovering old ones; contracts handled directly by this
session, since the cross-chain execution decision is safety-critical. Full ledger, including the
corrected build design for the new execution script and a sanity check on the reported profit number:
`docs/notes-cross-chain-arbitrage-stress-test.md`.

**Live environment re-verified fresh, not assumed from notes.** The Base `FlashLoanArbitrage`
deployment from §14 (`0x17fB2Da9D6b6f95962Ad21f39aAE43f40Caaf602`) is confirmed still live
(`aavePremiumBps()`→5, `paused()`→false). Balances unchanged in kind from §13/§14: Base ~0.00345 ETH,
Polygon ~0.091 POL, Arbitrum dust (0.0000023 ETH), Optimism 0. `contracts/deployments/` is absent in
this fresh container (git-ignored by design — expected, not a defect). Foundry got further than any
prior session — `foundryup` downloaded all binaries at 100% for the first time — but failed attestation
verification; declined `--force` (skips SHA verification, labelled insecure by the tool itself) given
this container holds a real operator private key, so `forge` remains BLOCKED locally, recorded with
this more precise detail rather than faked.

**14 confirmed defects fixed**, each with a regression test, every gate independently re-run green
both before and after (884 → 954 tests):

1. **Engine, HIGH (data-integrity) — phantom profit via an unvalidated bridge fee.** `BridgeQuote`'s
   `fee_bps`/`fixed_fee`/`settle_seconds` had zero validation, unlike every sibling domain object. A
   negative `fee_bps` made `net_after()` return *more* than the bridged amount — manufactured,
   `verified:true`-reported profit from a config typo, reachable since the ingestion-side field has no
   lower-bound check either. Fixed with `__post_init__` validation (mirroring `PoolState`/`Blockstamp`)
   plus a Hypothesis property test (`net_after(amount) <= amount` for any constructible quote) and a
   schema-level `Field(ge=0)` so a malformed HTTP request 422s at the boundary.
2. **Engine, HIGH — cross-chain dedup collision across chains.** `ranking.py`'s dedup key built a
   `frozenset` of bare pool-address strings with no chain tag — the same shape as the already-fixed
   ingestion bug (§12/I2), never checked on the engine side. Fixed: key now pairs `(chain_id,
   pool_address)` per leg.
3. **Engine, MEDIUM — cross-chain `min_profit_bps` ignored the destination chain's stricter
   threshold.** Only the buy-chain's per-chain override was ever applied. Fixed: now
   `max(buy_ctx.min_profit_bps, sell_ctx.min_profit_bps)`, strictly more conservative than either side
   alone.
4. **Ingestion, CRITICAL (core property closed) — no liveness watchdog on a chain's WS `heads`
   subscription.** The single most significant open finding carried across *two* prior audit cycles
   (§11 recorded it CRITICAL and live-reproduced; §12 didn't re-attempt it). An upstream node's WS
   could go quiet without ever erroring, so nothing in the old `select!` loop could ever notice —
   `verified:true` could go stale indefinitely with zero signal, a direct violation of the `verified`
   honesty invariant (§2 item 7). Especially costly for cross-chain trades, which are exposed to two
   chains' staleness risk over a multi-minute non-atomic settle window. Fixed: a new `stale_after()`
   threshold (30s floor, 20×block-time above it) plus a watchdog branch in the `select!` loop that
   returns `Err` to reuse the existing, already-tested supervisor reconnect path. Deliberately *not*
   covered: `/health` and the `CHAINS_LIVE` gauge still don't reflect a stalled-then-reconnecting chain
   in real time — needs restructuring `pipeline.rs::run()`'s startup order, recorded as a follow-up
   rather than rushed on a safety-relevant path.
5. **Ingestion, HIGH — HTTP endpoint failover never actually triggered for a genuinely dead
   endpoint,** its primary documented use case (recorded in §11, not fixed until now). Every RPC read
   method collapsed any transport error straight to a generic call-error variant, so a raw connection
   failure was never recognised as failover-worthy. Fixed with a `classify()` helper using `alloy`'s
   own `is_transport_error()`; proven with an empirical test that connects to a closed local port and
   confirms the real production path now fails over.
6. **Dashboard — MetaMask-signed Pause/Unpause kill-switch panel**, closing §12's O2 recommendation
   now that a real contract is actually deployed on Base (§14) to make it concrete rather than
   speculative. Same sanctioned pattern as deploy: the backend only serves the read-only compiled ABI
   and re-probes `paused()` after the tx confirms; it never signs. A live-state `paused: boolean |
   null` is now part of the readiness sweep, never guessed `false` when unreadable.
7. **Dashboard, MEDIUM (misleading-honesty class) — cross-chain opportunities were indistinguishable
   from same-chain ones in the opportunities table**, and the Execute button's tooltip claimed a
   simulated fill or live broadcast that a cross-chain row never actually performs (it's always
   recorded `"skipped"`, per §12's D2). Fixed: destination chain + settle time now shown (or an honest
   "unresolved" when ambiguous — never guessed), tooltip corrected.
8. **Dashboard, LOW — `NETWORK_COLORS` missing `unichain`/`ink`**, undermining fix #7 (a cross-chain
   row touching either chain rendered indistinguishable gray dots). Fixed.
9. **Launcher, HIGH — the same TOML-injection class §9/§10 already fixed for `pool_registry` was never
   applied to `ws_url`/`http_url` in `l2arb setup`** — the two fields an operator actually hand-pastes
   from an RPC dashboard. Verified exploitable through the real CLI before fixing.
10. **Launcher, HIGH — `config_is_live_ready()`'s placeholder-marker list missed the shipped example's
    Unichain V4 fields**, so a user who filled every other placeholder was told the config was
    live-ready and `run --live` would launch against a fake address. Fixed with a principled check (any
    `0x`-prefixed token containing a non-hex character can only be a placeholder), defending future
    placeholder shapes too.
11. **Launcher, MEDIUM — `payload.ensure_payload()` not safe under a concurrent double-launch of the
    `.exe`.** Fixed (already caught by the crash net, but a needless scary traceback for a non-failure).
12. **Launcher, LOW — `proc.run()` never explicitly closed its subprocess pipe.** Fixed.

**Contracts — no defects found; HEAD re-verified clean.** `allowedBridgeAdapters`, `siblingExecutor`,
route contiguity, the `GENERIC` allowlist, and profit-receiver-to-event-log consistency all re-checked
directly against source, all still correct. This session's contracts work was entirely about **proving
execution**, not fixing bugs.

**Cross-chain execution — what was run.** The existing dual-fork proof
(`test/fork/CrossChainDualFork.test.js`) re-confirmed passing live against real Polygon+Arbitrum state.
Beyond that, a new script, `contracts/scripts/live_cross_chain_fork.js`, extends the §13
`live_flash_loan_fork.js` precedent to the two-leg cross-chain model: the executor spends real,
organically-funded USDC.e inventory to buy WETH on a real, momentarily-and-honestly-dislocated Polygon
QuickSwap pool, bridges it (simulated — no real `IBridgeAdapter` exists, see §12/C1), sells it at
Arbitrum's real, untouched Uniswap V3 price, and sweeps the result to the real operator wallet via the
guardian-gated rescue path. Result: bought 2.185768 WETH with 3,790.54 USDC.e on the dislocated pool,
sold it for 4,074.86 USDC.e at Arbitrum's real price — **net +284.32 USDC.e delivered to
`0x50A71dF7DfC5850e8434C7c8A564366F4980183b`**. Sanity-checked before accepting: the script's own
honest counterfactual (pre-dump reserves, same input, no external price feed) shows the undislocated
round trip would have netted roughly *−45 USDC.e* — confirming the reported profit is genuinely
attributable to the disclosed manufactured dislocation, not a measurement artifact. Wired into CI
alongside the same-chain script, behind the same RPC secrets.

**Why a REAL (broadcast) cross-chain execution was not attempted — not a judgment call, a hard
technical fact.** `MockBridgeAdapter`, the only `IBridgeAdapter` implementation anywhere in this repo,
"pulls tokens, emits an event, delivers nothing cross-chain" by design. A real broadcast through it
would pull real funds on the source chain with **no delivery mechanism on the other side** — not a
revert-and-lose-gas outcome like the same-chain case (§13), a **permanent, irreversible loss**. That
alone rules out any real attempt regardless of gas or deployment state; compounding it,
`CrossChainArbitrageExecutor` is deployed on zero chains, Arbitrum/Optimism gas remains
dust-to-nothing, and no off-chain orchestrator exists (§12/C3, correctly Phase-9-gated). The new
fork-based proof above is the strongest safe alternative; a real bridge integration is real,
substantial, security-sensitive scope that deserves its own explicit conversation with the operator,
not a speculative same-session build.

**Net result:** 14 confirmed defects fixed (4 engine, 2 ingestion — including closing the core safety
property of this repo's longest-standing recorded CRITICAL finding, 4 dashboard, 4 launcher); one
further ingestion gap (degenerate-zero pool seeding across V2/V3/V4, broader than previously scoped)
recorded rather than rushed. All five gates re-run green throughout (884 → 954 tests). The cross-chain
dual-fork proof now has a stronger, wallet-targeted sibling proving genuine, honestly-disclosed profit
capture end-to-end on two live chains — the first time this repo has demonstrated that. What remains
before a *real* mainnet cross-chain trade is possible is unchanged in kind from §12 but sharper in its
most important particular: the blocker isn't just "the orchestrator is Phase 9" — it's that attempting
one today, even with a perfect route, would strand real funds in a bridge adapter that delivers
nothing. That single fact should anchor whichever future session picks up the real bridge integration.
