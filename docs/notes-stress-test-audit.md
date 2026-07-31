# Notes — granular stress-test & enhancement audit (branch `claude/stress-test-audit-pm9eya`)

Task: run a granular, production-grade stress test + enhancement audit so every
feature and function is verified correct and operational. Following the
tdd-dev-loop discipline (understand → audit → fix in tested batches → re-verify).

## Baseline gate state (verified this session, 2026-07-31)

| Component | Gate | Result |
|-----------|------|--------|
| engine (Python) | `uv sync --all-extras && make check` | ✅ 440 passed, 99.96% cov |
| ingestion (Rust) | `cargo fmt --check && clippy -D warnings && cargo test` | ✅ 185 passed, 0 warnings |
| contracts (Solidity) | Hardhat `npm test` (37 JS tests) | ✅ 37 passing |
| contracts (Foundry) | `forge test` | 🚫 BLOCKED — `forge` not installed in env |
| dashboard (Node/TS) | `pnpm install && pnpm verify` | ✅ 76 backend + 28 frontend, build clean |
| launcher (Python) | `python3 -m unittest discover -s launcher/tests` | ✅ 76 passed |

**Total: 842 tests green** across all runnable gates (Foundry suite BLOCKED, not faked).

`forge`/Foundry is unavailable in this environment, so `contracts/scripts/verify.sh`
and the Foundry suite (`test/ArbExecutor.t.sol`, `test/foundry/`) are BLOCKED, not
faked. The Solidity contracts are still exercised by the 37-test Hardhat suite.

## Audit method

5 parallel deep-audit subagents (one per component), each given the safety
invariants and asked for CONFIRMED vs SUSPECTED findings with concrete failure
scenarios. Every finding is independently re-verified by me before any fix.
Only real, traced defects get fixed; fixes land with tests; gates re-run green.

## Findings ledger (5 parallel audit agents → my independent verification)

Severity as verified by me. **FIX** = fixing this session with a test; **RECORD** =
reproducible finding logged for follow-up (not fixed this session, stated honestly).

### Dashboard
1. **CRITICAL — FIX** — `qualifies()` drops *every* external opportunity: `engineMap`
   sets `route[].dex = shortPool(leg.pool)` (a pool address) but `qualifies()` filters
   `leg.dex` against `s.dexes` (venue keys, default `["uniswap-v3",…]`, `min(1)`). The
   flagship `DATA_SOURCE=external` path (what `l2arb run --live` uses) surfaces zero
   opps. Engine JSON carries no venue label, so a venue chip cannot honestly filter it.
   VERIFIED end-to-end (engine schema → engineMap → qualifies → tick → launcher env).
2. MEDIUM — FIX — `qualifies()`/daily-loss apply USD thresholds to non-USD numeraire
   magnitudes (ignores `numeraireIsUsd`). Latent (masked by USDC default + #1).
3. LOW/MED — RECORD — external buffer not pruned while `engineEnabled=false` (bounded).
4. LOW — RECORD — envelope `schema_version` never validated by consumer.
5. LOW — RECORD — e2e latency over-sampled per-opportunity (observability skew).
6. LOW — RECORD — manual `POST /api/execute/:id` never checks expiry (paper-only impact).
7. LOW — FIX — `maxDailyLossUsd=0` halts all execution from t0 (`0 <= 0`). Foot-gun.
8. LOW/cosmetic — FIX — cooldown alert prints full budget, not time remaining.

### Contracts
1. **MEDIUM (fund-safety) — FIX** — discontinuous route + `amountIn>bal→bal` cap lets a
   compromised EXECUTOR drain any parked non-`asset` token via typed dexes, bypassing the
   GENERIC allowlist. Fix: enforce route continuity (`steps[i].tokenIn==steps[i-1].tokenOut`).
   VERIFIED by full accounting trace.
2. MEDIUM — RECORD — `CrossChainArbitrageExecutor` GENERIC hops un-allowlisted +
   arbitrary `bridgeAdapter` approve/drain. Partly inherent to an inventory contract.
3. LOW — RECORD — CURVE/GENERIC `.call` to codeless address silently no-ops (mitigated
   by minOut/profit guard — no drain).
4. LOW — RECORD — leftover GENERIC approval residue (bounded to allowlisted routers).
5. INFO — RECORD — OptimalArbitrage Yul overflow at ~1e38 reserves (view/advisory path).

### Engine
1. **HIGH — FIX** — `-math.log(0.0)` (`graph/rategraph.py:122`) crashes the whole
   `/detect` batch when an imbalanced StableSwap pool's `marginal_rate()` floors to 0.0
   (`amm/stableswap.py`). Reachable on shipped chains; no try/except in `detect()`.
2. HIGH (config-gated) — FIX — cross-chain 2-hop emits huge phantom profit + `verified:true`
   when numeraire/asset decimals differ across chains (`detect/cross_chain.py`). Guardless.
   Not reachable on the 5 shipped chains (consistent decimals) → add an explicit guard.
3. LOW-MED — FIX — cross-chain gates verified/freshness *after* pricing; docstring +
   DATA_INTEGRITY.md claim *before*. Align with same-chain `evaluate()`.
4. LOW — RECORD — freshness gate defeatable by caller-supplied `now_ts` (by-design replay).
5. LOW/SUSPECTED — RECORD — StableSwap Newton returns last iterate if unconverged.
6. LOW/by-design — RECORD — V3 single-tick estimate emitted `verified:true`, only note-flagged.

### Launcher
1. **HIGH — FIX** — Windows: `setup` writes `pool_registry = "C:\Users\…"` into a TOML
   *basic* string; backslashes are invalid escapes → `TOMLDecodeError`. Breaks the live
   path on the flagship `.exe`. Masked by a POSIX-only test.
2. MEDIUM — FIX — `Service.stop()` leaks a zombie on the SIGKILL escalation (no reap
   after `killpg(SIGKILL)`).
3. MEDIUM — FIX — no SIGTERM handler → `kill`/`docker stop` orphans children (started
   with `start_new_session=True`).
4. MEDIUM/SUSPECTED — FIX — restarted service loses startup grace (`ever_healthy` stays
   True) → self-heal crash-loop risk on slow cold-start.
5. LOW-MED — RECORD — synchronous restart blocks the monitor tick up to `grace`.
6. LOW-MED — RECORD — no port pre-flight before start.
7. LOW — FIX — orphan window: `webbrowser.open`/`HealthMonitor()` sit outside run.py's
   cleanup try (a non-OSError there orphans all three services).
8. LOW/SUSPECTED/Windows — RECORD — SIGKILL kills only direct child, not the tree.
9. LOW — FIX — fd leak when `Popen` raises inside `Service.start()` (log fh never closed).
10. LOW/doc — FIX — stale comment: health_bind :9090 is now served.

### Ingestion
1. **HIGH — RECORD (fix BLOCKED on real engine)** — `retain_valid`
   (`engine-client/src/validate.rs:57-93`) builds its verified-pool + sent-stamp sets
   from `req.pools`, which in incremental mode (the default) is only the changed-pool
   delta (`pipeline.rs:538-541` `s.pools = changed`). Any opportunity routing through a
   pool unchanged this tick is dropped as `LegPoolNotVerified`/`BlockstampNotInRequest`
   → the feed goes near-silent in steady state. CONFIRMED by code trace.
   **Recommended fix:** validate the response against the mirror's *full* verified pool
   set (captured before the delta reduction at `pipeline.rs:528-529`), decoupled from
   what the incremental request sends — a leg through a mirror-verified pool is genuinely
   trusted; the delta was an over-narrow proxy. **NOT fixed this session:** it is a change
   to a safety-critical validation path whose end-to-end correctness (esp. the blockstamp
   check) depends on the engine's exact incremental/stateful behavior, and the real
   `l2arb` engine is BLOCKED in this environment (ingestion CLAUDE.md §10), so the fix
   cannot be verified e2e here. Shipping it speculatively could forward phantoms or still
   drop valid opps — recorded rather than faked.
2. **HIGH — FIX (partial; refinement RECORDed)** — L1 data fee sampled with an EMPTY
   tx (`context.rs` passed `Bytes::new()` to `getL1Fee`) → under-costs L1 DA fee on the
   4 OP-Stack chains → phantom profit (prime directive 1); `hold_back_reason` only
   catches exact 0. Fixed the clear bug: now samples a **real recorded 53-byte tx**
   (`representative_sample_tx()`), pricing a genuine non-zero DA floor (~0.57 gwei on
   Base vs ~0 for empty) instead of nothing. RECORD follow-up: the sample models a
   minimal transfer, so a long multi-hop arb bills more — sizing the sample to a
   representative arb (ideally config-driven, notes finding B) is the remaining
   refinement, cushioned meanwhile by `gas_safety_multiplier`.
3. MEDIUM — FIX — WS accept loop `break`s permanently on any transient `accept()` error
   → no new subscriber can ever reconnect. Log-and-continue instead.
4. MEDIUM — RECORD — in-range V3/V4 Mint/Burn/ModifyLiquidity never ingested → stale
   liquidity re-stamped to head as `verified:true`. Larger change.
5. MEDIUM — FIX — reconnect backoff never `reset()`s on a healthy connection → recovery
   latency pins at the 30s ceiling for the process lifetime.
6. LOW-MED/SUSPECTED — RECORD — reseed generation bump ordered after seeding → one-tick
   window can send incremental when a full request is mandated.
7. LOW — RECORD — two declared metrics never emitted (slow-consumer drops invisible).
8. LOW — NOT-A-BUG — WS not newline-delimited: dashboard parses per-message (verified),
   so correct in practice. Documented only.
9. LOW/doc — RECORD — "HTTP-polling fallback" documented but absent (unreachable with a
   valid WS config; validation requires ws_url).

## Outcome

**18 confirmed defects fixed**, each with a regression test where testable; the
lower-severity remainder recorded above as a triaged, reproducible backlog. Every
component gate re-run green after its batch:

| Component | Gate | Before | After |
|-----------|------|--------|-------|
| dashboard | `pnpm verify` | 76+28 | **83+28** (+7), build clean |
| contracts | Hardhat `npm test` | 37 | **39** (+2) |
| engine | `make check` | 440 | **442** (+2), 99.96% cov |
| launcher | `unittest` | 76 | **80** (+4) |
| ingestion | fmt+clippy+test | 185 | **186** (+1) |

Commits (one per component, each green):
- `fix(dashboard): restore the external (production) data path + honest USD/venue gating`
- `fix(contracts): enforce route contiguity to close a held-token drain vector`
- `fix(engine): stop a degenerate StableSwap from crashing /detect; guard cross-chain unit + gate-order bugs`
- `fix(launcher): repair Windows live-config + close process-lifecycle leaks/orphans`
- `fix(ingestion): close L1-fee under-cost, WS accept death, and stuck reconnect backoff`

### Method note (honesty)
- The Foundry/`forge` suite and the live e2e smoke are BLOCKED in this environment
  (no `forge`; no outbound L2 RPC / real engine). Recorded as BLOCKED, never faked.
- No test was deleted, skipped, `xfail`'d, or loosened to pass. No synthetic market
  data was introduced in any runtime path (the L1-fee sample is a real recorded tx).
- Findings that required the unavailable engine to verify end-to-end were RECORDED
  with a concrete recommended fix rather than shipped speculatively.
