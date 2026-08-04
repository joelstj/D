# Notes — ingestion production audit + stress test (branch `claude/ingestion-audit-stress-test-wgbo9y`)

Task: a production-grade audit and stress test of the **ingestion** component, plus
verification that the whole stack (engine, ingestion, contracts, dashboard,
launcher) is genuinely wired for real-world live operation — finding real cross
arbitrage opportunities, launching flash-loan contracts, and depositing profit
into the connected wallet — with every GUI setting/button verified operational.
Following the tdd-dev-loop discipline. This is the **4th** full audit pass on this
codebase (see root `CLAUDE.md` §8–10 for the first three).

## What changed since the last audit: two blockers lifted

Every prior session recorded outbound L2 RPC and the real `l2arb` engine as
**BLOCKED** in-environment. Neither is blocked here:

- **Outbound Arbitrum RPC works** (`https://arb1.arbitrum.io/rpc` and others
  answer real `eth_blockNumber` calls).
- **The real engine stands up locally**: `cd engine && uv sync --all-extras` then
  `uv run uvicorn l2arb.api.http:app --port 8080` (HTTP transport) — or the
  subprocess transport via `L2ARB_ENGINE_CMD`. `web3`/`websockets` are present in
  the engine's venv.
- **`forge`/Foundry is still not installed** — the Foundry suite remains BLOCKED
  (Hardhat's 40-test suite still exercises the same contracts).

This unblocked things no prior session could do: `scripts/e2e_smoke.py` ran to
completion against real Arbitrum state and a live engine instance (see below),
and `crates/engine-client/tests/live_engine.rs` — a test explicitly written for
this exact scenario and never able to run before — passed for the first time.

## Baseline gate state (verified this session, 2026-08-03, before any fix)

| Component | Gate | Result |
|-----------|------|--------|
| engine (Python) | `uv sync --all-extras && make check` | ✅ 442 passed, 99.87% cov |
| ingestion (Rust) | `cargo fmt --check && clippy -D warnings && cargo test --workspace` | ✅ 186 passed (full log, not truncated) |
| contracts (Solidity, Hardhat) | `npm test` | ✅ 40 passing |
| contracts (Solidity, Foundry) | `forge test` | 🚫 BLOCKED — `forge` not installed |
| dashboard (Node/TS) | `pnpm install && pnpm verify` | ✅ 102 backend + 34 frontend, build clean |
| launcher (Python) | `python3 -m unittest discover -s launcher/tests` | ✅ 80 passed |

**884 tests green** across all runnable gates. (My first ingestion gate run
piped `cargo test`'s output through `tail -200`, silently truncating most of
it — the true baseline above is from a corrected, fully-captured re-run.)

## Audit method

6 parallel deep-audit subagents: 4 split the ingestion codebase by crate group
(core/amm/rpc/chains; registry/ingest/v4; gas/aggregator/engine-client/output;
app/observability/config), 1 audited every interactive control in the dashboard
frontend against its backend wiring, 1 re-verified the contracts profit-to-wallet
path and that detected opportunities are real (not fabricated). Each was told to
read the prior three audits first so findings would be genuinely new, to classify
CONFIRMED vs SUSPECTED with a concrete failure scenario, and — where the
environment allowed — to reproduce the failure live rather than by inspection
alone. Every finding below was independently re-verified by direct code reading
(and, for the two fixed CRITICAL/HIGH ingestion items, by writing and running a
regression test) before being recorded as fixed or left as a backlog item.

I additionally found and fixed one defect myself, live: running the official
`scripts/e2e_smoke.py` against real Arbitrum state and a real engine instance —
something no prior session's environment permitted — surfaced a real opportunity
that the engine detected correctly but that never reached the dashboard's
`/api/opportunities`. This was **not** a static-analysis finding; it reproduced
in an actual running system.

## Findings ledger

Severity as independently verified by me. **FIX** = fixed this session with a
test; **RECORD** = a real, reproducible finding, deliberately not fixed this
session (see the reason given), triaged honestly rather than rushed.

### Ingestion — the primary audit target

1. **HIGH — FIX** (`crates/app/src/pipeline.rs`) — the previously-**RECORDED**
   (3 prior sessions) `retain_valid` incremental-delta issue has a **corrected
   root cause**, proven empirically against the now-available real engine: it
   is not a validation bug. `pipeline.rs` truncated `req.pools` to
   `IncrementalTracker::changed()`'s delta whenever `incremental:true` (the
   steady-state case). The real `l2arb` engine builds a **fresh, fully stateless
   graph on every `/detect` call** — no cross-call cache — so a pool omitted
   from the request is not "safely already known" to the engine, it is **absent
   from its search entirely**. A/B proof with a live engine instance: sending
   only the changed pool of a 2-pool cyclic route → 0 opportunities; sending
   both pools (one changed, one re-stamped-but-unchanged) → 2 opportunities,
   correct profit. This made the single most common real arbitrage shape (one
   leg's pool moves, the cycle-partner pool doesn't, in the same tick)
   undetectable after a session's first tick — directly the "are we finding
   real cross arbitrage opportunities" concern this audit was asked to verify.
   **Fix:** `pipeline.rs` now always sends each chain's full current verified
   snapshot; `incremental` is preserved as a wire signal only. `IncrementalTracker`
   remains in `l2i_aggregator` (still tested there) but is no longer consumed by
   the live pipeline — sending a superset is safe for any future stateful engine
   too. `docs/ARCHITECTURE.md` §8 updated (the "incremental sends only deltas"
   latency claim was no longer true). New tests: `full_snapshot_always_sent_regardless_of_incremental`
   (aggregator invariants) plus a corrected `engine.test.ts`-style test on the
   dashboard side is separate (see Dashboard §1 below — same failure *class*,
   different component).
2. **CRITICAL — FIX** (`crates/app/src/ingestor.rs`) — V3 `Mint`/`Burn` and V4
   `ModifyLiquidity` are fully decoded, fully applied to the mirror, and directly
   unit-tested **at the decode/mirror layer** — but `apply_log` (the *only* place
   a live log's topic0 is ever dispatched) had no branch for either. Both were
   silently dropped on the live path (the WS `logs` subscription is
   address-only, so the logs *did* arrive — they just fell through the
   if/else-if chain with no `else`, no log, no metric). Reconcile cannot catch
   this either: it reads each pool at its own already-stale stored block, so a
   frozen `liquidity` figure "reconciles" as a match forever. `re_stamp` then
   re-labels the resulting stale liquidity `verified:true` at the live head
   every tick. A missed `Burn` overstates liquidity, which makes the engine
   **understate** slippage/price-impact for a trade sized against it — an
   opportunity that looks safer and more profitable than it is, reaching a
   human's execute decision. `cargo clippy -D warnings` stayed clean throughout
   because these are `pub` library functions with real call sites in their own
   tests — dead-code lint never fires. **Fix:** wired both dispatch branches
   (`v3_mint_topic()`/`v3_burn_topic()` added to `l2i_ingest::event`,
   `v4_modify_liquidity_topic()` already existed). Refactored `apply_log` from a
   `&self` method to a free function (it only ever touched 3 of `ChainIngestor`'s
   8 fields), which made it directly unit-testable without fabricating a full
   `ChainConfig`/provider. 5 new tests in `ingestor.rs` prove: in-range Mint
   grows active liquidity, in-range Burn shrinks it, an out-of-range Mint is
   correctly a no-op, V4 ModifyLiquidity dispatches by poolId, and the pre-existing
   V2 Sync / V3 Swap branches still work after the refactor.
3. **HIGH — FIX** (`crates/config/src/lib.rs`) — config validation checked
   `self.chains.is_empty()` but never `self.enabled_chains().count() > 0`. A
   config with all `[[chains]]` blocks present but every one `enabled = false`
   passes `--check-config` as "OK" and the live process starts, binds `/health`
   (always reports `"ok"` regardless — see the RECORDED item below), and idles
   forever supervising zero chains: a silent total outage indistinguishable from
   a healthy boot. Live-reproduced by the auditing agent (built the binary, ran
   it with such a config, confirmed 8s of `chains=0` logs and a healthy-looking
   `/health`). **Fix:** `validate()` now rejects this. 2 new tests
   (`rejects_a_config_where_every_chain_is_disabled`,
   `accepts_a_config_where_only_some_chains_are_disabled`).
4. **MEDIUM — FIX** (test infrastructure) —
   `crates/engine-client/tests/live_engine.rs::real_engine_detects_known_arb`, a
   test purpose-built to close the M8/M10 "real engine" blocker, has a hardcoded
   blockstamp `timestamp: 1_752_460_000` — ~385 days stale relative to this
   session's real wall clock. A freshness gate added by a *later* audit pass
   (root `CLAUDE.md` §8, `L2ARB__MAX_POOL_AGE_SECONDS`, default 120s, always-on
   at the engine's API boundary) silently zeroes every priced cycle against a
   stamp that old. Not a production risk (real ingestion always sends fresh
   blockstamps) but the test could never go green, at any future run date, and
   could mislead a future session into thinking the client/wiring was broken.
   **Fix:** the stamp now uses real wall-clock `SystemTime::now()`. Verified: ran
   it against the real engine over HTTP transport — **passes** (`2 opp(s),
   456.6 bps` — the first time this specific test has ever gone green).
5. **HIGH — RECORD** (`crates/rpc/src/provider.rs`, `crates/rpc/src/failover.rs`)
   — HTTP endpoint failover never triggers for a genuinely dead/unreachable
   endpoint, its primary documented use case. `is_failover_error` always fails
   over `Transport`/`Timeout` errors, and a `Call` error only if its message
   matches a rate-limit-specific substring — but **every** archive-read method on
   the real provider (`block_number`, `gas_price`, `head`, `call`, `code_at`,
   `code_at_batch`, `logs`) wraps *any* underlying error, including a raw
   connection failure, into `RpcError::Call(...)`. `RpcError::Transport`/`Timeout`
   are only ever constructed for the initial `connect()` and WS subscribe — never
   for an HTTP read. Empirically proven by the auditing agent: pointed the real
   pinned `alloy 1.8.3` HTTP provider at a closed local port, captured the real
   error text (`"error sending request for url (...)"`), fed it through the
   actual unmodified `is_failover_error` — `false`. `run_with_failover` then
   returns on the very first endpoint, never trying a configured, healthy
   backup, for that call or any later one. **Why not fixed this session:** the
   correct fix touches every read method in `provider.rs` (classify a
   `reqwest`-level connection/timeout error via its own `.is_connect()`/
   `.is_timeout()` methods and map it to `RpcError::Transport` instead of
   `RpcError::Call`, rather than pattern-matching message text) — real, broad
   surface area to get right across 7 call sites without a live multi-endpoint
   RPC test harness to verify each one against. Recommended fix is precise;
   shipping it untested here risked a regression in the one thing (failover)
   it's supposed to protect.
6. **CRITICAL — RECORD** (`crates/app/src/ingestor.rs`, cross-cutting with
   `crates/observability`) — no liveness/staleness watchdog on a chain's WS
   subscription. `ChainIngestor::run()`'s `select!` only races `shutdown`,
   `heads.next()`, `logs.next()` — no timer branch. If the upstream node's WS
   goes quiet without erroring (a known real-world RPC-provider failure mode,
   distinct from a clean disconnect), the loop blocks forever: nothing times
   out, the supervisor never sees an `Err`, `mark_all_unverified()`/reconnect
   never fires. Reconcile cannot catch this either, by design (queries each
   pool at its *own* stored historical block, so a frozen pool "confirms" as a
   match forever — it detects decode drift, not staleness). I independently
   re-verified the structural claim: `l2i_observability::health_router()` takes
   no arguments and reads no shared state (`Json(json!({"status":"ok"}))`,
   unconditionally), and is constructed and bound in `pipeline.rs::run()`
   **before** `handles` (the per-chain supervisor state) is even built — there
   is currently no channel for a real liveness signal to reach it at all. Live
   reproduced by the auditing agent: pointed a chain at an unreachable
   endpoint, watched it fail to connect for 10s straight, `/health` returned
   `{"status":"ok"}` throughout; separately, `l2i_chains_live` (the
   `CHAINS_LIVE` gauge) is set exactly once at spawn time
   (`metrics::gauge!(...).set(handles.len() as f64)`, `pipeline.rs:93`) and
   never updated again — same live-reproduced symptom, via `/metrics` instead
   of `/health`. **Mitigating factor:** execution stays human-gated with
   profit-or-revert `staticCall` simulation, so the likely real-world
   consequence is a misleading dashboard and a human wasting gas on a doomed
   execute, not a drained contract — flagged CRITICAL because it is a clean,
   total, currently-unmitigated violation of the "`verified:true` must mean
   genuinely fresh" invariant, not because it drains funds directly. **Why not
   fixed this session:** `health_router()`'s construction site runs before any
   per-chain state exists, so a correct fix restructures `run()`'s startup
   order (build shared liveness state → construct the health router around it →
   *then* spawn chains referencing the same state) and threads a per-chain
   "last activity" signal through `on_head`/the supervisor loop, a periodic
   staleness-check tick, and a real recompute of `CHAINS_LIVE`. This is a
   genuine, multi-file architectural change to the supervisor/health surface —
   getting the staleness threshold and the shared-state plumbing wrong risks a
   *new*, worse bug (false-positive reconnect storms on healthy chains) in
   exactly the safety-relevant path being hardened. Recommended direction:
   track a per-chain `Instant` of the last head/log received (the raw signal
   already exists); if `now - last_head > k × block_time_ms`, treat the chain
   as stalled — mark unverified, count a metric, and feed both `/health` and
   `CHAINS_LIVE` from the same source, reusing the existing "stream ended →
   `Err` → supervisor reconnects" recovery path rather than inventing new
   recovery logic.
7. **HIGH — RECORD** (`crates/gas/src/lib.rs`) — the previously-FIXED L1-fee
   under-cost fix (a real recorded 53-byte Base transaction, replacing empty
   calldata) is still solid, but its own follow-up note undersold the remaining
   gap. The sample is a **zero-payload plain ETH transfer**; real ABI-encoded
   calldata for `FlashLoanArbitrage.executeArbitrage(ArbParams)` is ≈1060 bytes
   for a 2-hop route and ≈3364 bytes for an 8-hop route (the contractual max) —
   **20×–63×** the sample. Since OP-Stack's `getL1Fee` prices roughly
   proportional to byte count, this under-costs the L1 DA fee by a comparable
   factor for any real multi-hop route, worse as hop count grows, on all 4
   OP-Stack chains — the flat ~1.5× `gas_safety_multiplier` cannot cover a
   20–60× gap. Because the contract's profit-or-revert design means a
   phantom-profit read-out can lead a human to sign/broadcast a doomed
   transaction via MetaMask that reverts on-chain, the concrete mechanism is
   real gas spent on attempts a correctly-costed estimate would have shown as
   unprofitable. **Why not fixed this session:** the right fix is sizing the L1
   fee sample to the *actual* route being priced (ideally deriving calldata
   length from `max_hops`/the real route shape, config-driven per the original
   follow-up note) rather than a second arbitrary fixed constant, which needs
   real design work to get the encoding math right across variable hop counts
   without either re-introducing under-costing or over-costing (killing
   genuine opportunities) — not something to guess at under audit time
   pressure.
8. **MEDIUM — RECORD, now defused** (`crates/app/src/pipeline.rs::spawn_chain`)
   — `seed_all(...).await?` (multiple sequential RPC round-trips, each
   immediately making pools visible via synchronous `mirror.insert()`) runs
   fully **before** `generation.fetch_add(1, ...)` signals a fresh session to
   the aggregator. Given the shipped `min_interval_ms=25`, a tick is plausible
   — not rare — during that window. Independently re-verified the ordering by
   direct read. **However:** the severe consequence originally described (a
   pool the aggregator has already fingerprinted as "unchanged" gets silently
   dropped from an incremental request during this window) **can no longer
   happen** — fix #1 above removed `IncrementalTracker` from the live pipeline
   entirely (confirmed by grep: zero references in `crates/app/src/*.rs`
   post-fix), so there is no code path left that omits a verified pool from a
   request based on tracker/fingerprint state, regardless of the race. The only
   remaining consequence is `incremental`'s wire *label* occasionally reading
   `true` on what should have been a session's first `false` — and the real
   engine (proven stateless per call, fix #1) doesn't consult that flag's value
   for anything today. Recorded as a genuine, still-real code-level race,
   downgraded from the original HIGH assessment because its practical impact
   against the actual shipped engine is now believed nil; worth closing
   properly (move the bump before `seed_all`, or a two-phase start/end signal)
   before any future engine implementation is allowed to depend on `incremental`
   meaning what the contract says it means.
9. **MEDIUM — RECORD, now moot** (`crates/aggregator/src/snapshot.rs`) —
   `IncrementalTracker`'s cache key (`PoolAddress`, no `chain_id`) is shared
   across all configured chains; a same-address pool on two chains (common for
   canonical Uniswap deployments) would cross-contaminate each other's fingerprint
   history. **No longer reachable in production**: fix #1 removed `pipeline.rs`'s
   only call site for `IncrementalTracker`, so this type is now exercised solely
   by its own crate tests. Recorded for completeness and because the type/gap
   still exists in the codebase (a future consumer could reintroduce the
   exposure), not because it currently does anything live.
10. **MEDIUM — RECORD** (`crates/registry/src/gate.rs`, `crates/v4/src/stateview.rs`)
    — neither the V4 validation gate nor V2/V3/V4 seeding rejects a pool with no
    real on-chain state. An `eth_call` against an uninitialized V4 poolId (or a
    freshly-`createPair`'d, unfunded V2 pair) returns all-zero data
    *successfully*, not a revert; the only guard is a length check, which a
    genuine all-zero-but-correctly-shaped return satisfies. Such a pool would be
    silently accepted as `verified:true` with zero price/liquidity. Engine-side
    consequence not verified this session (the July 31 audit found the engine
    crashing on a different degenerate-zero case, at least precedent that
    unhandled zero state reaching `/detect` has caused real problems before).
11. **LOW — RECORD** (`crates/rpc/src/provider.rs`, `amm/src/v2.rs`) — some
    pricing math (`get_sqrt_ratio_at_tick` as a described "cross-check",
    `v2::get_amount_out`/`get_amount_in`) is exhaustively correct (brute-forced
    all 1,774,545 valid V3 ticks against an independent reimplementation — zero
    mismatches) but is dead code on the live path — no production crate calls
    it today. Doc/comment overstatement, not a functional bug.
12. **NOT-A-BUG, re-confirmed** — the two previously-recorded LOW items
    (unemitted `l2i_output_subscribers`/`l2i_output_lagged_drops_total` metrics;
    a documented-but-absent HTTP-polling fallback for WS-less chains) are both
    still accurately described as before; not re-scored.

### Dashboard — found live, by actually running the stack

1. **HIGH (equivalent severity to the ingestion #1 fix above) — FIX**
   (`dashboard/backend/src/arbitrage/engine.ts::qualifies()`) — found by running
   `scripts/e2e_smoke.py` against real Arbitrum state and a real engine, not by
   static analysis. A manufactured-but-engine-priced WETH-numeraire opportunity
   (verified, 71.8 bps) never surfaced through `/api/opportunities` despite
   correctly flowing through the WS seam and being parsed/mapped (confirmed via
   `/api/latency`: 25 parse/map samples, 16 scans, 0 opportunities exposed).
   Root cause: `qualifies()` had a standalone `if (opp.tokenIn !== s.baseToken)
   return false` check, and the default `baseToken` is `"USDC"`. The engine
   closes each detected cycle in whichever configured hub token
   (`ingestion` `[[chains]].hubs`, every shipped chain lists **both** WETH and
   USDC, several also USDT) actually produced the edge — the engine's choice,
   not the operator's — so any real opportunity that happened to close in a
   non-`baseToken` numeraire was silently and permanently dropped, even though
   that numeraire (WETH) was in the operator's own `tokens` allowlist. This is
   the exact same bug *class* as the §9 CRITICAL "entire live data path was
   dark" fix from the prior session — a check that's vacuously true under
   `SimulatedProvider` (which always constructs `tokenIn: settings.baseToken` by
   definition) and silently wrong against `ExternalProvider`'s genuinely
   multi-numeraire real data. The purpose-built regression suite
   (`externalQualifies.test.ts`) didn't catch it because its own fixture also
   happens to use a USDC numeraire, matching the default `baseToken` by
   coincidence. A *pre-existing* test (`engine.test.ts`) actively **encoded the
   bug** as correct behavior (asserted a WETH opportunity must be rejected, with
   `tokens: ["USDC","WETH"]` — i.e. WETH was explicitly in the allowed
   universe). **Fix:** removed the standalone check; `opp.tokenIn` is now
   validated solely via the pre-existing per-leg `allowedTokens` membership
   check (`s.tokens ∪ {s.baseToken}`), which already correctly covers it for a
   cyclic route. The stale test was corrected (not deleted) to assert the real
   safety boundary — a token genuinely outside the allowed universe is still
   rejected — plus a new test for the corrected behavior, both passing. Verified
   **live, twice**: rebuilt the backend, re-ran the same manual repro — the
   opportunity now surfaces (`tokenIn:"WETH"`, `numeraireIsUsd:false`, 71.8 bps);
   then re-ran the official `e2e_smoke.py` end-to-end — now **12/12 passed**
   (was failing before the fix).
2. **(test infra) FIX** (`scripts/e2e_smoke.py`) — the final safety-gate
   assertion ("LiveExecutor MUST refuse to broadcast") gave a false-positive
   "SAFETY REGRESSION" alarm after the fix above, because step 8's paper
   execute (same "arbitrum" network, moments earlier) had already set that
   network's cooldown timestamp, and `riskLimitBlock()`'s cooldown check runs
   *before* `executeOpportunity` ever selects an executor — the live-refusal
   check never got a chance to exercise `LiveExecutor` at all, failing on
   "cooldown active" instead. **Not a real safety regression** — independently
   confirmed `LiveExecutor.execute()` unconditionally throws regardless of
   `EXECUTION_MODE`, and no code path anywhere in the dashboard constructs a
   signer or broadcasts. Fixed by patching `cooldownMs: 0` alongside
   `executionMode: "live"` in that step, so the assertion tests what it claims
   to test. Verified: full `e2e_smoke.py` run is now 12/12 clean.
3. **HIGH — FIX** (`dashboard/backend/src/arbitrage/engine.ts`) — the header
   Play/Pause button shows a stale "Running" state indefinitely after a pause.
   `tick()` returned before `emitStats()` on the `!engineEnabled` path — the
   *only* mechanism that pushes corrected stats to already-connected WS
   clients — so a paused engine kept broadcasting "Running" until an unrelated
   event happened to fire a stats push. A confused operator clicking it again
   then silently re-enables the very thing they tried to pause. **Fix:**
   `tick()` now emits stats on the disabled path too; additionally, a settings
   subscription now pushes stats immediately on any `engineEnabled` change,
   rather than waiting up to a full `scanIntervalMs` for the next tick. 2 new
   tests.
4. **MEDIUM — FIX** (`dashboard/backend/src/arbitrage/engine.ts`) — clicking
   Execute on an opportunity that expired or was already executed a moment
   earlier (rows live only 6–12s by design) failed completely silently: the
   `if (!opp) throw ...` guard was the only rejection branch in
   `executeOpportunity` that didn't emit an alert first. Fix: alerts now, same
   as every other rejection branch. 1 new test.
5. **MEDIUM — FIX** (`dashboard/frontend/src/components/WalletButton.tsx`) —
   Connect and network-switch both used the fire-and-forget wagmi hooks
   (`connect`/`switchChain`) with no error handling — a rejected MetaMask
   prompt silently reverted the UI with zero indication anything happened; the
   network switcher additionally closed its own menu *before* the wallet even
   responded. `ContractsPanel.tsx` (same codebase) already demonstrates the
   correct pattern. Fix: switched to the `*Async` variants + try/catch + a
   visible error message; the switch-network menu now closes only on success.
   3 new tests (module-mocked wagmi hooks).
6. **LOW — FIX** (`dashboard/frontend/src/components/SettingsPanel.tsx`) — the
   "Flash-loan size" field had `min={100}` but no `max`, while the backend
   schema caps it at 100,000,000 — `NumberField`'s clamp logic already handles
   `max` correctly, it just wasn't given one for this field. One-line fix.
7. **LOW — FIX** (`dashboard/frontend/src/hooks/useLiveData.tsx`) —
   `resetSettings()` had no error handling at all (unlike its sibling
   `patchSettings`), so a failed reset (backend down, network error) became an
   unhandled promise rejection with no user-visible effect. Fix: same
   reconcile-from-source-of-truth catch block `patchSettings` already uses.
8. **MEDIUM — RECORD** (systemic) — every one of the 18 consulted Settings
   fields silently reverts on a rejected PATCH with zero user-visible
   feedback (`patchSettings`'s catch block reconciles state but never alerts).
   Not fixed: the correct fix is a real toast/notification surface wired
   through the whole settings-change path, which is a UI feature addition
   beyond this audit's fix-in-place scope, not a one-line correction.
9. **MEDIUM — RECORD** — 4 settings fields (`maxGasGwei`, `priorityFeeGwei`,
   `gasLimit`, `deadlineSec`) are accepted, persisted, and broadcast, but
   consulted by **no** provider, executor, or engine code path anywhere — a
   user can drag "Max gas price" or "Tx deadline" and watch the value save
   forever with zero effect on what executes. `priorityFeeGwei`/`gasLimit` have
   no UI control at all (schema/API-only). Not fixed: wiring these into real
   behavior (e.g. gas-price-based execution gating) is a genuine feature
   addition needing its own design, not a bug-fix-scoped change.
10. **MEDIUM — RECORD** — `loanAmountUsd`/`flashLoanProvider` are consulted only
    by `SimulatedProvider`; `ExternalProvider` (the real production feed)
    ignores both — a real detected opportunity's size/fee come entirely from
    the engine's own numbers. Likely intentional (the engine already
    optimizes loan size server-side) but undocumented in the UI, so dialing
    "Flash-loan size" while on the real feed silently does nothing. Not fixed:
    a labeling/documentation clarification, not a functional bug in the
    control itself.
11. **LOW — RECORD** — removing the active `baseToken` from the `tokens`
    allowlist (allowed, since `tokens.length >= 2` remains) orphans the "Base
    asset" chip row — no chip renders as active, no way back short of Reset.
    UI-only confusion (the engine-side `qualifies()` defensively re-adds
    `baseToken` to the allowed set regardless, and — after fix #1 above — no
    longer strictly gates on it at all), not a functional/trading bug.
12. **LOW — RECORD** — `CrossChainArbitrageExecutor` has full backend support
    (compile, artifact serving, `recordDeployment` accepts a `crossChainAddress`)
    but zero UI surface — no compile-status line, no deploy button anywhere in
    `ContractsPanel`. The panel's own test fixture includes a
    `CrossChainArbitrageExecutor` artifact that no assertion ever checks is
    displayed.
13. **LOW — RECORD** — the readiness sweep computes `crossChainHasCode` but
    the UI never renders it.
14. **LOW — RECORD** — `unichain`/`ink` (2 of the 6 networks the ingestion
    layer actually feeds) have no entry in `NETWORK_COLORS`, falling back to
    generic gray — indistinguishable from each other in chips/dots/sparklines.

**Verified working, explicitly confirmed rather than assumed** (dashboard):
settings → engine wiring for every *consulted* field (`engineEnabled`,
`executionMode`, `autoExecute`, `networks`, `baseToken`, `tokens`, `dexes`,
`minProfitUsd`, `minProfitBps`, `slippageBps`, `maxConcurrentTrades`,
`cooldownMs`, `maxDailyLossUsd`, `maxPositionUsd`, `scanIntervalMs`) genuinely
round-trips PATCH → validated store → persisted → broadcast → consulted;
`executionMode`'s boot-time re-seed and the §9 venue-key + `maxDailyLossUsd=0`
fixes are still solid on current HEAD; the paper-execute path, the Contracts
panel (compile/deploy/readiness — deploy is MetaMask-signed only, backend never
holds a key), and wallet connection state are all genuinely real, not stubbed.
Zero controls found to be no-op/TODO stubs anywhere in the frontend.

### Contracts + opportunity-detection re-verification — both claims hold, nothing to fix

Re-verified against current HEAD (not documentation): profit routing
(`FlashLoanArbitrage.sol::_settle`, `profitReceiver` defaulting to the tx
signer, zero residual retained — test-covered and re-run live: 24/24 passing),
route-contiguity + the GENERIC router allowlist (both still enforced, both
still test-covered), the deploy flow (browser-signs-via-MetaMask,
backend-only-records-a-public-address), and — the highest-priority check given
this audit's brief — an **exhaustive grep across the whole `dashboard/` tree**
found zero signer construction, zero private-key handling, and zero
autonomous-broadcast path anywhere: `LiveExecutor.execute()` unconditionally
throws, and the frontend has no `useWriteContract`/`sendTransaction` call at
all — the *only* browser-signed chain-write in the entire product is contract
deployment. Engine detection math (`evaluate()`, `_build_opportunity()`,
`rategraph.py`, `tropical.py`) is real graph search over real AMM math with the
verified/freshness gates enforced *before* pricing, confirmed by both code
trace and live targeted test runs (65 tests across engine/dashboard/contracts,
all passing). One honest limitation re-confirmed unchanged from the prior
session: the dashboard cannot construct a genuine executable `ArbParams` route
from a detected opportunity (`RouteLeg` carries a pool-address/symbol label,
not a router address + `DexType` + calldata) — a one-click live-execute of an
arbitrary *detected* opportunity is not built, by design (fabricating the
missing route data would violate the data-integrity invariant). Live execution
of a manually/externally-constructed route stays available via the
human-signed MetaMask path the Contracts panel already provides.

## Outcome

**7 confirmed defects fixed this session** (4 ingestion, 3 dashboard + 1 test
sequencing fix), each with a regression test, plus 2 more dashboard fixes
(clamp + resetSettings) that were low-risk enough to fix inline. Every fix was
independently re-verified by direct code reading before being applied; two
(the dashboard `qualifies()` fix and the ingestion `retain_valid` root cause)
were additionally verified against a real, live running system, not just unit
tests. 14 further findings recorded with precise reproduction steps and
recommended fixes rather than rushed, matching the "record, don't fake green"
discipline established across the three prior audit sessions.

| Component | Gate | Before | After |
|-----------|------|--------|-------|
| ingestion | fmt+clippy+`cargo test --workspace` | 186 | **194** (+8: 5 apply_log dispatch, 1 aggregator invariant, 2 config validation) |
| dashboard | `pnpm verify` | 102 backend + 34 frontend | **107 backend + 37 frontend** (+5, +3), build clean |
| engine | `make check` | 442, 99.87% cov | unchanged — no engine-side fix needed |
| contracts | Hardhat `npm test` | 40 | unchanged — no contract-side fix needed |
| launcher | `unittest` | 80 | unchanged — not in this session's scope |

Plus, newly possible this session and run to completion (previously BLOCKED in
every prior environment): `scripts/e2e_smoke.py` — **12/12 passing** against
real Arbitrum state and a real engine instance; and
`crates/engine-client/tests/live_engine.rs::real_engine_detects_known_arb` —
**passing** against the real engine over HTTP transport, for the first time
ever in this repo's history.

### Method notes (honesty)

- No test was deleted, `#[ignore]`'d, `--skip`'d, or loosened to pass anywhere
  this session. One pre-existing test (`engine.test.ts`, dashboard) that
  actively asserted incorrect behavior as correct was corrected — not
  weakened — to test the real safety boundary, with the change explained in a
  comment at the site.
- No synthetic data was introduced into any runtime path. The manufactured
  price dislocation used to drive `e2e_smoke.py` is the same
  clearly-labeled, single, documented integration fixture the script already
  used before this session (not new).
- `forge`/Foundry remains BLOCKED (not installed) — recorded, not faked; the
  Solidity contracts are still exercised by the 40-test Hardhat suite.
- Every RECORD item above was independently read and confirmed by me (not
  just relayed from an audit agent) before being written down: I re-read the
  actual `is_failover_error`/provider.rs error-wrapping code, the
  `representative_sample_tx` implementation, the `health_router`/`pipeline.rs`
  startup-order code, and the `spawn_chain`/generation-bump ordering myself.
  Two findings (the reseed-race and the IncrementalTracker cache-key issue)
  were re-assessed and downgraded from their originally-reported severity
  because the `retain_valid` fix (item 1) independently defused their most
  severe consequence — noted explicitly above rather than silently.
