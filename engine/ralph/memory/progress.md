# Progress log (append-only)

Newest entries at the top. One entry per completed iteration. Format:

```
## <ISO date> — <task id> <short title>
- Did: <what changed, 1–3 lines>
- Why: <the reason / which milestone it advances>
- Evidence: <tests/tiers that went green; benchmark numbers; coverage delta>
- Follow-ups: <new backlog items filed, if any>
```

Keep it factual and terse — this is how the next fresh iteration knows the true
state of the world. Do not delete history.

## 2026-07-14 — docs — refresh for delivered families/verify/optim + final gate
- Did: Brought the docs current with the delivered engine. ARBITRAGE_THEORY: Curve
  StableSwap + Balancer weighted un-marked "stretch" (now delivered, with the
  never-drain clamp noted); V3 QuoterV2 cross-check noted as realized bit-for-bit;
  sizing updated to memoized golden-section. TESTING_STRATEGY + DATA_INTEGRITY:
  documented the realized bit-exact verify tier, the adversarial/stress tier, and
  the Blockscout `read_contract` float-precision caveat (capture via raw eth_call).
  ARCHITECTURE: amm module map updated (stableswap/weighted/quote delivered).
  README status rewritten to reflect four AMM families, Timescale store, backtest,
  numba, and live bit-exact on-chain verification.
- Why: CLAUDE.md §6 — docs change in the same breath as behaviour; stale docs are bugs.
- Evidence: `make ci` exit 0 — lint + mypy --strict + pairing + 397 tests @ 100%
  line+branch coverage + pip-audit (no known vulns) + bandit all green.
- Follow-ups: T-0308 (Curve/Balancer real-onchain verify), T-0603/T-0604 (event-path
  pooling + p99 SLO CI gate), Phase 8 oracle module remain open in the backlog.

## 2026-07-14 — T-0208,T-0304 — real-onchain verify + adversarial/scale stress
- Did: Captured **real, live** Base (chain 8453) pool state at a pinned block via
  Blockscout raw `eth_call` (exact hex) into `tests/verify/fixtures/base_onchain_pools.json`:
  a Uniswap V2 WETH/USDC pool and a Uniswap V3 WETH/USDC 0.05% pool, each with
  independent-oracle probes (`getAmountsOut` / QuoterV2 `quoteExactInputSingle`).
  `tests/verify/test_onchain_amm.py` (verify tier) rebuilds each pool through the
  real `pool_from_dict` ingestion boundary and asserts `quote.amount_out` matches
  the on-chain oracle **bit-for-bit** — V2 across 5 sizes/both directions, V3 both
  directions incl. post-swap sqrtPrice. Added `tests/stress/` — adversarial pool
  states (uint112 ceiling, 1-wei, 99.99% fee, V3 price extremes, extreme amp/weights)
  as property tests, plus whole-engine soundness on a hostile graph and a 64-token
  scale graph (soundness + ranking + determinism + incremental==full).
- Why: "Production grade real live world testing" — prove the engine reproduces
  real chain behaviour and never fabricates an edge under hostile input.
- Evidence: `make check` green — 397 tests (was 363), 100% line+branch coverage,
  mypy --strict + ruff clean. Fixed a **real over-statement bug** the stress suite
  surfaced: Balancer `amount_out` could round up to the full out-balance (impossible
  drain) at extreme weight ratios; now clamped to `balance_out - 1` with a pinned
  regression test. Marked T-0208 (V2 reserves vs router) and T-0304 (V3 vs QuoterV2,
  bit-exact) done; filed T-0308 for the Curve/Balancer live cross-check follow-up.
- Follow-ups: T-0308 (StableSwap/weighted real-onchain verify — needs A_PRECISION /
  rate-normalization care); live-fork `chain`-tier replay (needs an RPC/Anvil node).
  Next: docs refresh + final operational verification.

## 2026-07-14 — T-0601 — profile hot path + sizing/route optimizations
- Did: Profiled `engine.compute()`. Two enhancements from it: (1) added
  `constant_product.amount_out_unchecked` (skips fixed-value re-validation) and
  wired it into `detect/profit._compile_route`'s V2 hot path — the size search
  re-prices a route dozens of times per candidate, so re-validating constant
  reserves/fee each call was pure waste. (2) Replaced the ternary size search in
  `amm/sizing.maximize_unimodal` with a **memoized golden-section** search: ~1.8x
  fewer route evaluations, with a memo so the carried-over probe is a cache hit
  and probes are recomputed fresh each step (no integer-rounding drift).
- Why: Latency hardening (Phase 6). The size-search route eval is the detector's
  dominant per-candidate cost; fewer evals + a cheaper eval compound.
- Evidence: `make check` green — 363 tests, 100% line+branch coverage,
  mypy --strict + ruff clean. Micro-bench: golden-section 1.76x fewer f-calls on
  a V2 route (1.16x wall), 1.88x fewer / 1.59x wall on a stableswap-leg route
  (memo overhead is fixed, so the win grows with per-eval cost). Reported argmax
  cross-checked against a brute-force grid: exact global max.
- Follow-ups: T-0603 (object pooling / zero-copy event path), T-0604 (p99 SLO CI
  gate) remain open. Next: T-0702-adjacent — production stress + real-onchain
  (Blockscout) test suite.

---

## 2026-07-14 — T-0702/T-0901/T-0902/T-0905 — opportunity store + backtest
- Did: `store/pg_store.py` (async SQLAlchemy Core opportunity store; TimescaleDB
  hypertable on Postgres, guarded by dialect so the same code runs on in-memory SQLite
  in unit tests; big-int net_profit as text; save/save_many/recent/top_by_score/count;
  aiosqlite dev dep). `backtest/replay.py` (deterministic historical replay through a
  configured engine; snapshots carry their own time — no wall-clock) + `backtest/metrics.py`
  (lean stdlib summary: hit-rate, profit distribution, strategy mix, edge decay).
  T-0905 scope guard: runtime path may not import `l2arb.backtest`.
- Why: Persist reported opportunities for time-series analytics; replay history offline
  to characterise the engine — both requested. Analytics stays strictly offline.
- Evidence: `make check` green — 362 tests, **100% line+branch** whole tree. Store
  round-trips + ordering, replay determinism + per-block reporting, metrics aggregates
  all tested; backtest-in-runtime guard green. mypy --strict + ruff + audit clean.
- Follow-ups: docs; optimization audit; production stress + real-onchain (Blockscout) tests.

## 2026-07-14 — T-0602 — numba JIT for the tropical min-plus sweep
- Did: numba-@njit min-plus matmul kernel in `graph/tropical.py`, selected at import
  when numba is available (pure-numpy reference is the fallback + correctness oracle);
  `warmup()` to pay JIT compilation once at startup off the hot path. Numba body is
  `# pragma: no cover` (JIT machine code isn't line-traceable); correctness is pinned
  by a numba≡numpy bit-for-bit agreement test. Added a tropical latency benchmark.
- Why: The tropical full sweep is the O(V^3) hot loop; numba ~2x faster (V=100: 1.73ms
  numpy -> 0.86ms numba) with identical results.
- Evidence: `make check` green — 346 tests, **100% line+branch** whole tree. numba/numpy
  agreement + warmup + numpy-reference hand-check all tested. mypy --strict + ruff +
  audit clean.
- Follow-ups: Timescale store; backtest; docs; audit; live tests.

## 2026-07-14 — T-0306/T-0307 — Curve stableswap + Balancer weighted DEX families
- Did: `amm/stableswap.py` (Curve 2-coin get_D/get_y Newton, exact integer, -1 rounding);
  `amm/weighted.py` (Balancer 2-token out-given-in via high-precision Decimal ln/exp,
  floored so never overstated; closed-form marginal). Extended `model/pool.py` with
  `PoolKind.STABLESWAP/WEIGHTED` + `StableSwapState`/`WeightedState` + generalized
  exactly-one-state validation + oriented helpers. Wired both into `amm/quote.py`
  dispatch, `detect/profit.py` `_compile_route`/`input_capacity`, and `store/serde.py`
  round-trip. The JSON API accepts them automatically (pools are serde dicts).
- Why: Curve/Balancer hold major L2 stable + weighted liquidity — biggest cross-dex
  coverage win. Detectors now find arb across V2/V3/Curve/Balancer freely.
- Evidence: `make check` green — 342 tests, **100% line+branch** whole tree. Property
  tests (monotonic, bounded, low-slippage-near-balance, weight-skew marginal); cross-dex
  2-hop detection through stable+V2 and weighted+V2 pools; profit gate routes through
  both. mypy --strict + ruff + audit clean.
- Follow-ups: numba on tropical sweep; Timescale store; backtest; docs; audit; live tests.

## 2026-07-14 — latency: route compilation + benchmark tier (T-0012, Phase 6 start)
- Did: `tests/benchmark/test_engine_latency.py` (deterministic 24-token/70-pool graph,
  pytest-benchmark on `compute`); profiled the hot path and added
  `detect/profit._compile_route` — precompute each hop's exact-math step once so the
  size solver runs pure arithmetic (no PoolState/`.key`/`Leg` allocation per eval).
- Why: "Blazing fast." The size search dominated cost; compiling the route cut
  full-sweep compute **~3.7x (232ms → 63ms)** with identical results (T-0409 + all
  profit tests still green).
- Evidence: `make check` green — 305 tests, **100% line+branch** whole tree. Benchmark
  tier now exists (CI benchmark job runs it). mypy --strict + ruff + audit clean.
- Follow-ups: numba on the tropical min-plus sweep + closed-form 2-V2-hop sizing fast
  path (T-0602); p99 SLO gate (T-0604).

## 2026-07-14 — language-agnostic integration surface (T-0703 + runner)
- Did: `api/schema.py` (pydantic DetectRequest incl. per-chain gas/price context +
  cross-chain config; `opportunity_to_dict` output with big ints as decimal strings);
  `api/service.py` (build_engine + detect/run_detection — the single shared core;
  unpriced numeraire => infinite gas => never reported, no invented prices);
  `api/runner.py` (stdin->stdout JSON batch: `python -m l2arb.api.runner`, structured
  JSON errors, exit codes); `api/http.py` (read-only FastAPI: POST /detect, GET
  /health). Added `docs/INTEGRATION.md` documenting the JSON contract. httpx added to
  dev deps for the FastAPI TestClient contract tests.
- Why: The core goal — a plug-and-play drop-in calculation engine for Rust/Go/TS/
  Node/C#/C++/JVM backends. Subprocess pipes need zero bindings; HTTP for those who
  prefer it. Both share one contract so behaviour never diverges.
- Evidence: `make check` green — 303 tests, **100% line+branch** whole tree. Service
  finds+serializes opportunities (single + cross-chain), rejects unpriced numeraires
  and malformed requests; runner round-trips stdin/stdout incl. error JSON; HTTP
  /detect + /health contract-tested via in-process TestClient. mypy --strict + ruff +
  audit clean.
- Follow-ups: remaining backlog is Phase 1/2 live-RPC ingestion (external per the
  brief — bots feed the engine), Phase 6 latency/numba, Phase 8 verification subsystem,
  T-0702 Timescale store (db tier), Phase 9 analytics. Core calculation engine is
  feature-complete for M4/M5 + persistence + integration.

## 2026-07-14 — persistence & caching (T-0701 + memory cache + warm start)
- Did: `ports/store.py` (PoolCache/AsyncPoolCache Protocols, keyed by chain+address);
  `store/serde.py` (lossless JSON-safe encode/decode; big ints as strings; round-trip
  identity; bad payloads -> IngestError at the boundary); `store/memory_cache.py`
  (in-process latest-state cache with freshness/monotonicity + snapshot/from_snapshot);
  `store/redis_cache.py` (async Redis hot cache over a duck-typed client, unit-tested
  with a fake). Wired into the engine: `ingest` now routes through the cache (drops
  stale updates before they reach the graph) and `snapshot`/`load_snapshot` give
  warm-start persistence.
- Why: The brief explicitly asks for "persistent memory and caching." Cache = the
  engine's fast working memory; snapshot = durable persistence across restarts; Redis
  = cross-process sharing between the per-chain bots and the engine.
- Evidence: `make check` green — 285 tests, **100% line+branch** whole tree (added a
  coverage exclude for Protocol `...` stub bodies). Freshness, big-int fidelity,
  snapshot round-trip, and warm-start-equivalence all tested. mypy --strict + ruff +
  audit clean.
- Follow-ups: language-agnostic integration surface (JSON batch API over stdin +
  read-only FastAPI + opportunity serialization) so Rust/Go/TS backends drop it in.
  T-0702 (Timescale opportunity store) deferred — needs the db test tier (services).

## 2026-07-14 — T-0501..T-0504 — cross-chain 2-hop (Phase 5 / M5)
- Did: `model/canonical_asset.py` (AssetRegistry: which (chain,address) tokens are the
  same asset; conservative fungibility = same canonical id + both bridgeable; native
  vs bridged USDC.e never assumed 1:1); `detect/cross_chain.py` (BridgeQuote/BridgeModel/
  StaticBridgeModel + `cross_chain_two_hop`: buy cheap on X, bridge, sell dear on Y;
  net = gross - bridge - gas with transparent breakdown; settle_seconds + cross-chain
  risk penalty; sizing optimises true post-bridge profit). Wired into ArbitrageEngine
  via `configure_cross_chain` + `_detect_cross_chain` across ordered chain pairs.
- Why: Completes the requested strategy set (same-chain 2/3-hop, cross-dex, cross-chain
  2-hop). Milestone M5. Simple 2-hop only — no cross-chain cycles (scope).
- Evidence: `make check` green — 259 tests, **100% line+branch** whole tree. Detects
  planted spreads; rejects non-bridgeable assets, missing pools/quotes, no-spread, and
  gas-wiped cases. mypy --strict + ruff + audit clean.
- Follow-ups: persistence + caching (Phase 7 — user explicitly asked for "persistent
  memory and caching"): in-process state cache, Redis hot cache, Timescale opportunity
  store. Then the language-agnostic integration surface (JSON/stdin batch + FastAPI).

## 2026-07-14 — T-0410 + engine — ArbitrageEngine facade, top-N ranking (M4)
- Did: `engine/detection.py` (run all 3 detectors + gate over one graph; incremental
  via `sources`, partitioned by length so 2-hop/triangular/multi-hop barely overlap);
  `engine/ranking.py` (dedup by pool-set+numeraire, top-N by risk-adjusted score);
  `engine/engine.py` (`ArbitrageEngine`: per-chain graphs, `configure_chain` gas/price
  ctx + optional curated hubs w/ `top_hubs` auto-fallback, `ingest`/`ingest_many`,
  `compute(top_n, incremental)` full-sweep or dirty-seeded). Pure compute, no I/O/keys.
- Why: This is the plug-and-play calculation core — ingest live pool state, get the
  ranked top-N opportunities. **Milestone M4** ("the engine works") for single chain +
  cross-dex, all strategies (2-hop/3-hop/multi-hop).
- Evidence: `make check` green — 237 tests, **100% line+branch** across the whole
  tree. T-0410 incremental≡full-sweep equivalence pinned; multi-chain ranking, dedup,
  hub config, gate-rejection all tested. mypy --strict + ruff + audit clean.
- Follow-ups: cross-chain 2-hop (Phase 5: canonical assets + bridge cost); persistence
  & caching (Phase 7 — user explicitly asked); language-agnostic integration surface
  (JSON/stdin + FastAPI). Then push + open a fresh PR (PR #2 already merged to main).

## 2026-07-14 — T-0408/T-0409 — net-profit gate + MEV/frontrun risk model
- Did: `model/opportunity.py` (Opportunity/Leg/RiskAssessment/StrategyKind);
  `detect/profit.py` (exact re-price + optimal size via new `sizing.optimal_size_auto`
  auto-bracketing; net = out(s*)-s*-gas gate; GasModel = L2 exec + L1-DA -> numeraire
  via on-chain price + safety mult; MevModel scoring success-probability / capture /
  frontrun risk; risk-adjusted `score` for ranking). Slippage is exact (inside AMM math).
- Why: Turns candidate cycles into confirmed, net-profitable, risk-scored opportunities
  — the "top-N most profitable" substance the brief asks for, incl. slippage/gas/MEV.
- Evidence: `make check` green — 218 tests, **100% line+branch** on all core
  (amm/graph/detect/model). T-0409 property test pins "never reports a net loss";
  arb-free cycles rejected; gas/threshold gates verified. mypy --strict + audit clean.
- Follow-ups: engine wiring (T-0410 dirty/incremental, T-0411 cold-start->detect->rank)
  + top-N ranker across all detectors; then cross-chain (Phase 5), persistence/caching
  (Phase 7), and the language-agnostic integration surface.

## 2026-07-14 — T-0401..T-0407 — rate graph + all cyclic detectors (core)
- Did: `graph/rategraph.py` (in-place `-ln(rate)` multigraph, O(1) pool rewrite,
  decimal-adjusted human rates, dirty tracking, best-edge collapse);
  `detect/cycle.py` (candidate cycle helpers); `detect/two_hop.py` (spatial /
  cross-dex 2-hop); `detect/triangular.py` (hub-rooted 3-hop, rotation-dedup);
  `graph/negcycle.py` (Bellman-Ford + edge-predecessor recovery); `graph/tropical.py`
  (min-plus K-hop bounded pre-filter, numpy); `detect/multi_hop.py` (tropical roots
  + bounded DFS recovery). Added the `graphkit` test toolkit (tests on the path).
- Why: This is the detection engine — finds 2-hop, triangular, and bounded multi-hop
  arbitrage as negative cycles. Advances M4 (first "the engine works" milestone).
- Evidence: `make check` green — 199 tests, **100% line+branch** across `graph/`
  and `detect/`. Property test pins the `Σ-ln r<0 ⇔ Πr>1` identity; detectors find
  planted arbitrage and report NOTHING on arbitrage-free graphs (no false positives);
  hop bounds verified. mypy --strict + ruff + audit clean. TDD caught a real
  remove_pool ordering bug before commit.
- Follow-ups: profit gate (T-0408) + no-loss property tests (T-0409) next — turns
  candidates into net-profit-ranked opportunities incl. gas/slippage/MEV. Then engine
  wiring + top-N ranking. T-0304/T-0412 remain chain-tier (Anvil).

## 2026-07-13 — T-0301/T-0302/T-0303/T-0305 — exact AMM math + sizing (core)
- Did: `constants.py` (shared fixed-point protocol constants); `amm/constant_product.py`
  (exact V2 out/in-given, price-impact, post-trade reserves — bit-for-bit with
  `getAmountOut`/`getAmountIn`); `amm/concentrated_liquidity.py` (exact V3 single-
  tick swap via SqrtPriceMath rounding, conservative tick-boundary capping,
  marginal rate, price); `amm/sizing.py` (integer unimodal maximiser + closed-form
  two-pool optimum that cross-check); `amm/quote.py` (uniform pricing over
  PoolState dispatching to the right family). Property tests for all invariants.
- Why: This is the calculation core the whole engine computes on. Advances M3.
- Evidence: `make check` green — 155 tests; `amm/` at **100% line+branch**
  (constitution's bar for core math); mypy --strict + ruff clean.
- Follow-ups: T-0304 (QuoterV2 ≤1-wei cross-check) is a `chain`-tier task — blocked
  on Anvil (blocked.md). Next: graph (T-0401) + detectors (T-0403/4/7) + profit gate.

## 2026-07-13 — T-0006/T-0102/T-0202 — core value objects & error hierarchy
- Did: Added `errors.py` (DataError vs InfraError split, ADR-003), and the pure
  CORE value objects `model/blockstamp.py` (Blockstamp provenance stamp),
  `model/token.py` (Token identity + on-chain decimals), `model/pool.py`
  (`PoolKind`, `V2Reserves`, `V3Slot0`, `PoolState` with Blockstamp + verified,
  unified `fee_pips` millionths convention, orientation helpers). Corrected the
  now-merged branch name across CLAUDE.md/ralph to `claude/l2-arbitrage-engine-j4olzf`.
- Why: The whole computational spine (AMM math → graph → detect → engine) is
  typed on these objects. Advances M2/M3/M4 foundations.
- Evidence: `make check` green — 100 tests, 100% line+branch coverage on new
  modules, mypy --strict clean, ruff clean.
- Follow-ups: AMM math next (T-0301 constant-product), then V3 (T-0303), sizing.

## 2026-07-13 — SETUP — build plan & Ralph scaffold committed
- Did: Established the full build plan (`docs/`, `plan/`), the operating
  constitution (`CLAUDE.md`), the Ralph loop machinery (`ralph/`), tooling
  (`pyproject.toml`, `Makefile`, `.pre-commit-config.yaml`, CI), and a minimal
  green Python scaffold (`src/l2arb/`, `tests/`, guard scripts).
- Why: Bootstraps the loop so iteration #1 starts from a green tree and a clear,
  ordered backlog (Phase 0 → M0).
- Evidence: `make check` intended to be green on the scaffold; see M0 criteria in
  `plan/milestones.md` (verify on first real iteration and tick when confirmed).
- Follow-ups: Begin at `plan/backlog.md` T-0001. Note: Anvil not yet installed in
  the base image (needed for the `chain` tier — see `blocked.md`).
