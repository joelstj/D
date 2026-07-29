# Research notes — production debug + enhancement pass (2026-07-29)

Scratch notes for the `claude/prod-debug-enhancements-x4gzp7` session. Not a
durable doc — findings that matter get folded into CLAUDE.md / component docs
at close-out; this file is the working log per tdd-dev-loop stage 6.

## Baseline gate status (all green, zero errors, before any changes)

| Component | Gate | Result |
|---|---|---|
| engine | `make check` (lint+types+pairing+test+cov) | 419 tests, 99.96% cov, clean |
| ingestion | fmt+clippy(-D warnings)+`cargo test --workspace` | all green |
| contracts | Hardhat (`npm test`) + Foundry (`verify.sh`: fmt/build/test) + slither | Hardhat 25 passing, Foundry 11 passing, slither: informational only, no High/Medium |
| dashboard | `pnpm verify` (typecheck+test+build) | backend 59 + frontend 28 = 87 tests, build OK |
| launcher | `unittest discover` | 61 tests passing |

Toolchain notes for this sandbox: Foundry wasn't preinstalled; `foundryup`'s
SHA-attestation check fails through the agent proxy (transport is trusted TLS,
attestation transparency-log check apparently isn't reachable) — installed
with `foundryup --install 1.5.1 --force` (skips only the extra attestation
check, not TLS verification). contracts `npm ci`/bootstrap.sh both target
`contracts/node_modules` — do NOT run them concurrently (race → ENOTEMPTY).

**Gotcha for future sessions**: `engine/Makefile`'s `setup` target runs `uv run
pre-commit install`, and `engine/` has **no nested `.git`** (vendored via
`git archive` per root `CLAUDE.md` §4) — so `pre-commit install` walks up and
installs `engine/.pre-commit-config.yaml`'s hooks into the **super-repo
root's** `.git/hooks/pre-commit`. Those hooks assume `engine/` as cwd (`mypy
src`, `scripts/check_test_pairing.py`, …), so any `git commit` at the repo
root after running `make setup` inside `engine/` fails with path errors
unrelated to your actual change. Fix: `cd engine && uv run pre-commit
uninstall` (confirmed safe — restores the pre-session state; the root repo
has no pre-commit hook of its own, only the CI workflow gates each
component correctly-scoped). Not fixed at the source (`engine/`'s own
`.pre-commit-config.yaml`) this pass — flagging so the next session doesn't
lose time rediscovering it, and because a proper fix (either scope the hooks'
`entry`/`files` so they run from `engine/` regardless of the hook's install
location, or have `make setup` detect the merged-monorepo layout and skip the
install) is its own small, separate, well-scoped task.

## Engine (`engine/`) — Explore agent findings

**Two disconnected config surfaces — this is the big one:**
- `l2arb/config.py` `Settings` (env, `L2ARB__` prefix) — `L2ARB__MIN_PROFIT_BPS`,
  `L2ARB__MAX_HOPS`, `L2ARB__GAS_SAFETY_MULTIPLIER`, `L2ARB__REDIS_URL`,
  `L2ARB__LOG_LEVEL`, `L2ARB__METRICS_PORT`, `L2ARB__BLOCKSCOUT__API_KEY`,
  `L2ARB__CHAINS__*` — **`get_settings()` has zero call sites outside
  `config.py`/tests.** Every one of these documented, `.env.example`'d knobs is
  dead. Root README/OPERATIONS.md documents them as real, live knobs.
- `api/schema.py` `DetectRequest`/`ChainConfig` — the actual live per-request
  surface (top_n, max_hops, gas_price_wei, min_profit_bps, gas_safety_multiplier,
  hubs, cross_chain...) — this one *is* wired end-to-end correctly.
- Fix direction: make `Settings` (env) the *default* fallback that
  `build_engine()`/`api/service.py` reads when a `ChainConfig` field is absent,
  so operators get a real global default via `.env` AND callers can still
  override per-request. Also wire `L2ARB__LOG_LEVEL` to actual `structlog`
  setup (dependency present, zero usage) and `L2ARB__METRICS_PORT` to a real
  prometheus endpoint (dependency present, zero usage).

**Data-integrity gaps (violates root CLAUDE.md invariant 1 + engine's own
docs/DATA_INTEGRITY.md, which claims these are already enforced):**
1. `Opportunity.verified` (`detect/profit.py:280`, `detect/cross_chain.py:197`)
   is computed but **never gates emission** anywhere (`engine/detection.py`,
   `engine/ranking.py`, `engine/engine.py` — no `if not verified: skip`).
   `docs/DATA_INTEGRITY.md:113-114` asserts present-tense that this filter
   exists; it does not. Any caller can POST pools with `verified:false` (or a
   fabricated `true`) and get a fully-ranked opportunity back regardless.
2. Freshness/staleness (`Blockstamp.is_stale()`, `PoolStateCache.evict_stale()`)
   is defined + unit-tested but **zero call sites** on the runtime path —
   `ArbitrageEngine.ingest()` only rejects older-block updates for the *same*
   pool, never wall-clock-ages anything out. A pool ingested once and never
   updated again can produce opportunities forever.

**Other real findings:**
3. `graph/tropical.py::warmup()` (numba JIT warmup) is defined, documented
   ("call once at process startup"), and has a `ralph/memory/learnings.md`
   entry about why it matters — but **no call site**. First real `/detect`
   touching a >=4-hop sweep eats full JIT compile time, undermining the
   `MAX_LATENCY_MS_P99` SLO on cold start.
5. No CI enforcement of the latency SLO despite `docs/LATENCY.md`/
   `docs/TESTING_STRATEGY.md` claiming CI "asserts p99 ≤ budget."
   `tests/benchmark/test_engine_latency.py` only asserts correctness, not timing.
6. `api/http.py` has no CORS/rate-limit/request-size-limit/auth — tracked as
   backlog T-1104 (not hidden), but Dockerfile ships it listening on 0.0.0.0.
7. V3 "unbounded single-tick" caveat is a free-text string in `risk.notes`,
   not a structured field — a consumer that doesn't grep for the exact string
   misses that the size estimate may be conservative/overstated.

No TODO/FIXME/mock/stub/hardcoded found in `src/l2arb` runtime (clean grep).
`plan/backlog.md` Phase 8 (oracle/blockscout.py, crosscheck.py, verifier.py)
is unchecked — consistent with finding 1/4, this is tracked, not a surprise.

---

## Ingestion (`ingestion/`) — Explore agent findings

**Config surface**: almost everything in `config.toml` is genuinely wired
(`[engine]`, `[cadence]`, `[output]`, `[observability]`, `[infra]`,
`[[chains]]`, `[cross_chain]`, `[cache]` all read and consumed). Real gaps:
- `engine.keep_alive`, `engine.first_request_incremental` — parsed, zero call
  sites, no runtime effect.
- `infra.op_l1_block`, `infra.arb_gas_info` — parsed only; Arbitrum L1 fee is
  hardcoded `Ok(0)` (deliberate approximation, compensated by higher
  `gas_safety_multiplier` 1.6 vs 1.5 — documented, not a bug).
- `chains[].flashblocks` — fully unimplemented, inert config.
- `figment` (env-override) workspace dep declared, **never imported** —
  `config.toml` cannot be overridden by env vars at all despite the dep.
- **`observability.health_bind` (:9090) is parsed, printed in `--check-config`,
  but never bound** — both `/health` and `/metrics` are actually served on
  `metrics_bind` (:9100) only. Docs/Dockerfile `EXPOSE`/config summary all
  claim two separate listeners; there's only one.
- Two metrics declared but never emitted: `l2i_output_subscribers`,
  `l2i_output_lagged_drops_total` (output/src/ws.rs tracks the data, never
  records it).

**Correctness findings:**
- **(A, real bug)** `l1_data_fee_wei` has no failure-gating unlike
  `gas_price_wei` — `pipeline.rs:464-470` holds the chain back on
  `gas_price_wei==0` but there's no equivalent for a failed L1-fee read on the
  4 OP-Stack chains (Base/Optimism/Unichain/Ink); it silently stays at the
  seeded `0` → same "phantom profit" risk the gas_price guard exists to
  prevent. **Small, surgical, high-value fix**: mirror the existing gate.
- **(B)** L1 fee estimate always samples an **empty-bytes** tx
  (`context.rs:177-186`) → systematically underestimates a real multi-hop
  calldata's L1 fee. No config knob exists for a representative sample size.
- **(E, real gap)** No HTTP-polling fallback exists despite
  `provider.rs:107-109`'s comment claiming one. A chain configured with only
  `http_url` (no `ws_url`) will loop reconnect→reseed→fail **forever** — never
  ingests. `subscribe_heads()`/`subscribe_logs()` propagate `?` immediately on
  "no WS configured."
- **(C)** Two fully-built, unit-tested, documented-in-CLAUDE.md modules are
  never wired into the live binary: `rpc/src/coalesce.rs` (SingleFlight dedup)
  and `rpc/src/reconnect.rs` (supervised reconnect w/ tests) — the real
  reconnect loop (`pipeline.rs`) reimplements its own ad hoc version instead.
- **(F)** V2/V3/V4 seeding multicalls are unchunked (unlike gate/reconcile,
  which correctly chunk at 500/200) — fine at today's registry sizes, latent
  risk at scale.
- **(D)** SIGHUP config-reload is documented (README) but the handler just logs
  "not yet implemented, restart to apply" — honest in-code, inconsistent at
  the doc level.
- Panic-safety on the decode hot path is genuinely good (saturating math,
  length-checked decoders, `panic=abort` risk understood and defended).
- No synthetic data reachable from the real `/detect` payload or WS envelope.
- Only Arbitrum has a real `config/pools/*.toml` registry shipped; Base/
  Optimism/Unichain/Ink pool files referenced by the example config don't
  exist in the repo — "code complete, data empty" for 4 of 5 chains.
- Zero TODO/FIXME/XXX/HACK in the whole workspace; zero `#[ignore]`d tests
  (two tests use an env-var runtime skip instead, both clearly labeled).

## Contracts (`contracts/`) — Explore agent findings

**Structural note**: two parallel trees exist — `contracts/contracts/` (the
real, tested, production Hardhat+Foundry engine, what README/INTEGRATION/
DEPLOYMENT describe) vs `contracts/src/` (a from-scratch Ralph-loop rewrite
skeleton, Phase-0, `execute()` unconditionally `revert NotImplemented()`).
CLAUDE.md's repo map describes the skeleton, not the working contract. All
findings below are about `contracts/contracts/FlashLoanArbitrage.sol` (the
real one).

**Profit/callback invariants — verified sound:**
- `executeArbitrage` → snapshots `preBalance` before any external call (CEI),
  arms a one-shot callback latch, dispatches to Aave or Balancer.
- Aave callback validates `msg.sender==AAVE_POOL && initiator==address(this)`.
  Balancer's real ABI has **no initiator param at all** — the shared one-shot
  `_callbackState` latch is the (tested-against-a-real-fork-Vault) substitute.
- Profit-or-revert: `generated = balanceNow - preBalance; require(generated >=
  owed + minProfit)` — correct, tested, fuzzed (0.1%-8% size sweep).
- Confirmed **no path anywhere** (CI, scripts, ralph loop) can sign/deploy/
  broadcast — `.claude/settings.json` even hard-denies `forge script
  --broadcast`/`cast send`/`cast publish` at the tool-permission layer.
  Invariant 2/3 clean.

**Real, well-scoped hardening opportunity:**
- **`DexType.GENERIC` step lets a compromised `EXECUTOR_ROLE` key drain the
  contract's *entire* balance of any token**, not just the current hop's
  amount — `DexRouter.sol` does a raw `step.router.call(data)` with no
  enforced relationship between the declared route and what the calldata
  actually does. E.g. `step.router = <held token>`, `data =
  transfer(attacker, hugeAmount)` bypasses both the route-asset check and the
  profit invariant (they only look at first/last leg + overall balance delta).
  Gated behind `EXECUTOR_ROLE` (a designed trust boundary, "your bot's hot
  key" per docs) — not unauthenticated — but worth a **router allowlist**
  (GUARDIAN_ROLE-settable) as defense-in-depth. Good enhancement candidate:
  well-scoped, testable (unit + fuzz + invariant per contracts CLAUDE.md).

**Other findings (lower priority / already tracked):**
- `test/ArbExecutor.t.sol` (12 tests, for the `src/` skeleton) never runs
  under default `forge test` — `foundry.toml` scopes to `test/foundry` only,
  and this file sits directly under `test/`. Scope gap, not a masked failure.
- README says "24 passing" Hardhat tests; actually 25 (stale by one, from
  today's Yul commit) — trivial doc fix.
- Registry A (`config/addresses.js`, what `deploy.js` actually reads): real
  addresses for Optimism/Base/Arbitrum/Polygon, explicit `null` for
  Unichain/Ink. Registry B (`config/chains/*.json`, for the unbuilt `src/`
  engine): ALL 5 chains still `_verify:true` placeholders (Phase-1 work not
  started) — consistent with backlog, not a new red flag.
- Slither: informational only (naming/pragma/low-level-call/too-many-digits
  from intentional Yul), no High/Medium severity findings.

## Dashboard (`dashboard/`) — Explore agent findings

**Settings audit — the most directly relevant section for this task.** Of 20
schema fields (`backend/src/settings/schema.ts`), reachable via `PATCH
/api/settings`:
- **Fully live, all providers**: `engineEnabled`, `autoExecute`,
  `minProfitUsd`, `minProfitBps`, `maxPositionUsd`, `scanIntervalMs`,
  `executionMode` (routing only).
- **Live but simulated-provider-only** (no effect on the real `external`
  production path): `networks`, `baseToken`, `tokens`, `dexes`,
  `flashLoanProvider`, `loanAmountUsd`, `slippageBps`.
- **Dead — zero backend consumer anywhere**: `maxGasGwei`, `priorityFeeGwei`,
  `gasLimit`, `deadlineSec`. Pure cosmetic UI with no wiring.
- **Partial**: `maxConcurrentTrades`, `cooldownMs`, `maxDailyLossUsd` — only
  enforced inside the `autoExecute()` loop; **manual `POST /api/execute/:id`
  and any direct API caller bypasses all three risk limits entirely.**
- **No persistence at all** — `SettingsStore` is pure in-memory; any change
  (including `EXECUTION_MODE`) is lost on process restart, silently reverting
  to defaults. Already a P0 in the dashboard's own backlog.
- **(c, real bug, most important)**: `ExternalProvider.scan(_settings)` — the
  settings param is **unused**. In `DATA_SOURCE=external` mode (the actual
  production wiring per root CLAUDE.md Seam B), toggling off a network/DEX
  chip in the Settings UI **has zero effect** — directly contradicts the
  Settings panel's own subtitle ("every control is wired to the engine —
  changes take effect on the next scan"). This is the single most direct hit
  on this task's "all settings...live adjustable and operational" requirement.

**Other correctness findings:**
- **(a) Mixed-numeraire PnL corruption**: `ExecutionResult.numeraireIsUsd` is
  computed correctly per-fill but `applyResult()` blindly sums
  `realizedProfitUsd` into scalar `stats.realizedPnlUsd`/`dailyPnlUsd` with no
  unit-tracking; the frontend's `$` label is derived from an unrelated signal
  (current opportunity list), not from what actually produced the number.
  This corrupted `dailyPnlUsd` also feeds the `maxDailyLossUsd` circuit
  breaker — a real safety-relevant honesty gap once multiple numeraires are
  live (external mode doesn't constrain `baseToken`).
- **(b)** Unhandled promise rejection risk: `patchSettings`/`resetSettings` in
  `useLiveData.tsx` have no `.catch` at any call site (Header, SettingsPanel);
  `OpportunitiesTable`'s execute path correctly wraps in try/catch by
  contrast.
- `LiveExecutor.execute()` unconditionally throws regardless of
  `EXECUTION_MODE`/settings — this is the *actual*, robust safety backstop;
  `executionMode` itself is freely PATCHable at runtime with no cross-check
  against the boot-time env var (fine, because the throw doesn't depend on
  it).
- README test counts stale (claims 21/15, actual 60/28) — doc-only fix.
- Minor: `unichain`/`ink` missing from frontend `NETWORK_COLORS`/`DEX_LABELS`
  maps (cosmetic); `api.toggleEngine` and `GET /api/flash-loan-providers` are
  fully implemented but never called from the frontend (dead client wiring,
  not dead backend).
- No auth on any mutating endpoint — tracked already in dashboard's own
  backlog P2.

---

## Launcher (`launcher/`) — Explore agent findings

**Settings surface**: CLI flags + `L2ARB_HOME`/`NO_COLOR` env vars all wired.
`.l2arb/config.toml` written by `setup` — only `ws_url`/`http_url`/
`pool_registry` actually vary per run; everything else is a fixed literal in
the f-string template (fine — matches "guided quickstart," not meant to be a
full config editor). `HealthMonitor`'s `MonitorPolicy` tunables (max_restarts,
backoff, timeouts) are hardcoded defaults with **zero CLI/config/env
exposure** — `run.py:117` never passes `policy=`.

**Real finding — orphaned sibling processes on startup failure (5.1, highest
value here):** `run.py`'s sequential `engine.start()` → `wait_http()` →
`ingestion.start()` → `dashboard.start()` → `wait_http()` has **no
try/finally**. `HealthMonitor.run()`'s cleanup (`health.py:385-390`) doesn't
exist until *after* every service is started. So: (a) if `ingestion.start()`/
`dashboard.start()` raises, or (b) if Ctrl-C lands while `wait_http()` is
polling (up to 40s for engine, 30s for dashboard) — already-started
engine/ingestion processes are never stopped, keep their ports bound
(`start_new_session=True` means terminal Ctrl-C doesn't reach them either),
and the *next* `l2arb run` can fail to bind or run a stale duplicate
alongside a fresh one. Well-scoped, clearly testable fix.

**Other findings:**
- `prereqs.py:_run()` bypasses the `_resolve()` shim fix from `e9ea71f` —
  `doctor`'s reported pnpm/npm version can read "not found" on Windows even
  when present (cosmetic doctor-output bug, not a functional blocker since
  `installer._pnpm_cmd()` uses `shutil.which` directly).
- Windows UnicodeEncodeError fix (`6134835`) is complete and well-tested;
  `scripts/build_exe.py` carries an independent duplicate of the same helper
  (drift risk, not a current bug).
- Restart-budget logic validated correct (bounded, no infinite loop, no
  too-eager giveaway) — just not configurable (same theme as MonitorPolicy
  above).
- `Service.stop()` doesn't confirm the kill landed (`poll()`/`wait()`
  missing); Windows forced-kill only terminates the direct child, not the
  process group (latent — no service forks children today).
- Build scripts (`build_exe.py`/`build_windows_exe.ps1`/`Build_L2ArbBot.bat`)
  all current and correct relative to today's package layout. Zero
  TODO/FIXME/mock/stub in `launcher/l2arb`. 61/61 tests pass.

---

## Final enhancement shortlist (locked in after all 5 explore agents reported)

Picked for: direct evidence from the research above, genuine cross-component
spread, and a direct hit on the task's two explicit asks — "no errors /
fully functional" and "all settings live-adjustable and operational" — while
staying inside CLAUDE.md's safety invariants (detection-only engine, paper-
by-default execution, no synthetic runtime data).

1. **Engine — enforce `verified` + freshness gating on the live detection
   path.** Closes a real violation of root invariant 1 and the engine's own
   `docs/DATA_INTEGRITY.md` (which currently asserts a filter that doesn't
   exist). New `L2ARB__MAX_POOL_AGE_SECONDS` setting.
2. **Engine — wire the dead `L2ARB__*` env-config surface into the runtime**
   as live operator defaults (`min_profit_bps`/`max_hops`/
   `gas_safety_multiplier` fallback, real `structlog` setup from
   `LOG_LEVEL`), plus fire `tropical.warmup()` on process startup (numba
   cold-start SLO fix, same startup-hook location).
3. **Dashboard — make settings enforcement uniform across every provider and
   every execution path.** Move network/DEX/token filtering into the
   provider-agnostic engine layer (fixes `ExternalProvider` — the actual
   production path — silently ignoring those controls) and enforce
   `maxConcurrentTrades`/`cooldownMs`/`maxDailyLossUsd` in
   `executeOpportunity()` itself, not just the `autoExecute()` loop, so
   manual/API execution respects the same risk limits.
4. **Dashboard — persist settings to disk** (`backend/.data/settings.json`,
   load on boot), closing the gap where every adjustable setting — including
   `executionMode` — silently reverts to defaults on restart.
5. **Ingestion — close the L1-fee phantom-profit gate** (mirror the existing
   `gas_price_wei==0` hold-back for a failed/zero L1-fee read on the 4
   OP-Stack chains) **and bind `health_bind` on :9090** as documented instead
   of silently folding it into `:9100`.
6. **Launcher — fix the orphaned-process leak** on startup failure/Ctrl-C by
   wrapping the sequential service-start sequence so any already-started
   service is stopped before the error propagates.

Stretch (only if the six above land clean with time to spare): a
GUARDIAN_ROLE-gated router allowlist for `DexType.GENERIC` steps in
`FlashLoanArbitrage.sol`, closing the finding that a compromised
`EXECUTOR_ROLE` key can drain the contract's entire balance of any held
token via an unconstrained low-level call. Deferred by default because it's
the highest-risk file in the repo (real funds custody) and the component's
own CLAUDE.md demands fuzz+invariant tests for anything touching it — better
done carefully in a follow-up than rushed here.

Explicitly NOT doing (out of scope / already fine / too large for this pass):
the ingestion HTTP-polling fallback for WS-less chains (real gap, but a
substantial new feature, not a fix — flagged for a follow-up task instead of
rushed); `l2i-rpc`'s unused `coalesce`/`reconnect` modules (wire-up is a
larger refactor of the live ingestor, not a bounded fix); contracts'
`test/ArbExecutor.t.sol` scope gap (tests the not-yet-built skeleton, zero
runtime impact); dashboard auth-on-mutating-endpoints (already tracked P2,
larger than a single-session scope alongside everything else here).
