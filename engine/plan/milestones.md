# Milestones — phase acceptance gates

> The loop should not begin a phase's tasks until the previous phase's milestone
> passes, unless a task is explicitly independent. Each milestone is a checklist
> of **verifiable** conditions — a human or a test run can confirm every line.
> Mark a milestone `[x]` only when all its criteria are demonstrably true and the
> evidence (test run / benchmark / doc) is committed.

## M0 — Foundation ready
- [ ] `make ci` green on the scaffold (lint, types, pairing, tests, audit).
- [ ] Coverage gate active; floor recorded in `Makefile` (`COV_FAIL_UNDER`).
- [ ] `scripts/check_test_pairing.py` and `scripts/check_no_secrets.py` pass and
      have their own tests.
- [ ] `ralph/loop.sh` runs one full iteration → commit on a trivial task.
- [ ] `config.py` loads & validates from env; secret redaction verified.

## M1 — Chain connectivity
- [ ] Subscribes to ≥1 live L2; tracks head; `Blockstamp` on every read.
- [ ] Endpoint failover + WSS reconnect proven by operations tests.
- [ ] Reorg detection → correct invalidation set (forked `chain` test).
- [ ] No missed blocks across a simulated drop (gap backfill works).

## M2 — DEX state
- [ ] Live reserves for a real V2 **and** V3 pool set, updated from logs O(1).
- [ ] Each pool's state cross-checked vs the oracle at a pinned block (`verify`).
- [ ] Token `decimals` read on-chain; fee-on-transfer/rebasing tokens quarantined.

## M3 — AMM math
- [ ] Local V2 & V3 quotes match on-chain QuoterV2 / `getReserves` within
      tolerance (≤1 wei) at pinned blocks (`chain`/`verify`).
- [ ] `amm/` at 100% line+branch coverage; property tests green.
- [ ] `optimal_size` matches brute-force grid maximum (concavity holds).

## M4 — Single-chain detection  ⭐ ("the engine works", first form)
- [ ] Detects planted cycles in synthetic graphs; **zero** false positives on
      arbitrage-free graphs.
- [ ] Emits **verified, block-stamped, net-profitable** 2-hop, triangular, and
      multi-hop opportunities from **live/forked** state.
- [ ] Incremental (dirty-set) detection ≡ full-graph detection (equivalence test).
- [ ] End-to-end event→emit within p99 SLO on warm state (`benchmark`).
- [ ] Net-profit gate never emits a loss once gas+slippage applied (property test).

## M5 — Cross-chain 2-hop
- [ ] Canonical-asset fungibility map is on-chain-verified (native vs bridged
      distinguished; no silent 1:1 assumptions).
- [ ] Emits verified 2-chain spreads with bridge cost + `time_to_settle` + drift
      risk in the report. No cross-chain cycles exist in the code.

## M6 — Latency
- [ ] p99 event→emit ≤ `MAX_LATENCY_MS_P99`, enforced as a CI benchmark gate.
- [ ] Per-stage baselines stored; regression beyond tolerance fails CI.

## M7 — Persistence & API
- [ ] Opportunities + snapshots persist (Timescale) and round-trip (`db`).
- [ ] Read-only API + WS feed pass contract tests; retraction events delivered.

## M8 — Verification subsystem
- [ ] Continuous sampled two-source verification runs; disagreement flags
      `UNVERIFIED` and excludes from emission.
- [ ] A reported opportunity is reproducible from its provenance via the oracle
      (`verify`).
- [ ] Reorg → retraction proven end-to-end (chaos test).

## M9 — Backtesting & analytics
- [ ] Deterministic historical replay; metrics/tearsheet report produced.
- [ ] Static test proves `backtest/`/`cex/` are unreachable from the runtime
      detection path.

## M10 — Observability & ops
- [ ] Dashboards + alerts for latency, data-integrity, throughput.
- [ ] Health/readiness endpoints; non-root container; runbooks committed.

## M11 — Security
- [ ] `/security-review` clean; `pip-audit` + `bandit` green in CI.
- [ ] Threat model documented; no-keys/no-signing/no-synthetic-data static tests
      all green.

## Final — "the engine works" (all true at once)
- [ ] Cold start → subscribes to ≥2 live L2s within SLO.
- [ ] Maintains verified, fresh state for a configured pool set.
- [ ] Emits single-chain 2-hop/triangular/multi-hop + cross-chain 2-hop, each
      net-profitable, block-stamped, and independently re-verifiable.
- [ ] p99 latency ≤ SLO (gated). `make ci` green. Coverage floor met.
- [ ] No execution path, no keys, no synthetic runtime data (static tests prove it).
