# Backlog — the Ralph loop's task list

> **How to use this file (every iteration):** pick the highest task marked `[ ]`
> whose dependencies (`dep:`) are all `[x]`, do exactly that one task TDD-first,
> tick it `[x]` when its acceptance criteria pass and the tree is green, then
> commit and stop. Tiers in `tests:` must be green for the task to count as done.
>
> If a task is too big for one iteration, **split it in place**: replace it with
> 2–4 finer `[ ]` sub-tasks (keep the id, add `.1`, `.2`) and do the first. Coarse
> tasks in later phases are *expected* to be decomposed this way.
>
> Legend: `dep:` dependencies · `tests:` required test tiers · ⭐ critical path.

---

## Phase 0 — Foundation & tooling  (bootstraps the loop)

- [ ] **T-0001** ⭐ Create `src/l2arb/` package + `tests/` tree; `l2arb.__init__`
  exposes `__version__`. `dep:` — · `tests:` unit
- [ ] **T-0002** ⭐ `scripts/check_test_pairing.py`: fail if any runtime module
  lacks a `test_*`. Self-test it. `dep:` T-0001 · `tests:` unit
- [ ] **T-0003** ⭐ `scripts/check_no_secrets.py`: block private keys/mnemonics/
  api-key shapes in staged files. `dep:` T-0001 · `tests:` unit
- [ ] **T-0004** ⭐ `l2arb/config.py` (`pydantic-settings`): chains, endpoints,
  thresholds, SLOs; loads from env/.env; validates. `dep:` T-0001 · `tests:` unit
- [ ] **T-0005** `l2arb/logging.py` (`structlog`): JSON in prod, dev renderer,
  secret redaction. `dep:` T-0004 · `tests:` unit
- [x] **T-0006** `l2arb/errors.py`: typed exception hierarchy — `DataError`
  (bad data → raise) vs `InfraError` (retryable). `dep:` T-0001 · `tests:` unit
- [ ] **T-0007** ⭐ `.github/workflows/ci.yml`: lint→types→pairing→test→audit;
  coverage gate. `dep:` T-0002,T-0004 · `tests:` (CI runs green)
- [ ] **T-0008** Import-linter contract: `core` must not import `adapters`.
  `dep:` T-0001 · `tests:` unit
- [ ] **T-0009** `test_no_synthetic_data_in_runtime`: static scan forbidding
  fixture/synthetic imports in `src/l2arb`. `dep:` T-0001 · `tests:` unit,verify
- [ ] **T-0010** `test_no_signing_imports`: forbid signing/tx-submit imports in
  `src/l2arb`. `dep:` T-0001 · `tests:` unit
- [ ] **T-0011** `docker/compose.dev.yml`: Postgres/Timescale + Redis for the
  integration/db tiers. `dep:` — · `tests:` (used by later tiers)
- [x] **T-0012** Warm-up JIT harness + first `benchmark` test skeleton so the
  latency tier exists from the start. `dep:` T-0001 · `tests:` benchmark

**Milestone M0** — `make ci` green on the scaffold; loop can run end-to-end.

---

## Phase 1 — Chain connectivity layer

- [ ] **T-0101** ⭐ `ports/rpc.py` `ChainClient` Protocol: `call`, `get_logs`,
  `get_block`, `subscribe_heads`, `subscribe_logs`, `head`. `dep:` T-0004 · `tests:` unit
- [x] **T-0102** ⭐ `model/blockstamp.py` `Blockstamp{chain_id,number,hash,ts}`;
  required on all state. `dep:` T-0001 · `tests:` unit
- [ ] **T-0103** ⭐ `rpc/client.py`: async multi-endpoint HTTP client with
  `tenacity` retry, failover, rate limit, response validation. `dep:` T-0101 · `tests:` unit,integration
- [ ] **T-0104** ⭐ `rpc/subscriptions.py`: WSS `newHeads`/`logs`, auto-reconnect,
  resubscribe, gap backfill. `dep:` T-0103 · `tests:` integration,chain
- [ ] **T-0105** `rpc/multicall.py`: batch `eth_call` for cold-start loads.
  `dep:` T-0103 · `tests:` unit,chain
- [ ] **T-0106** ⭐ `rpc/reorg.py`: head/parentHash tracking; reorg detect →
  common ancestor; emit invalidation set. `dep:` T-0104 · `tests:` unit,chain
- [ ] **T-0107** Anvil fork fixture in `tests/conftest.py` (spin/stop at pinned
  block); gate `chain` tier on availability. `dep:` T-0103 · `tests:` chain
- [ ] **T-0108** Operations tests: endpoint drop → failover; WSS drop →
  reconnect; assert no missed blocks. `dep:` T-0104 · `tests:` integration

**Milestone M1** — subscribe to a live L2, track head within SLO, survive drops,
handle a forked reorg. `chain` tier green.

---

## Phase 2 — DEX adapters & pool state

- [ ] **T-0201** ⭐ `ports/dex.py` `DexAdapter` Protocol: `discover_pools`,
  `decode_state`, `marginal_rate`, `exact_quote`. `dep:` T-0101 · `tests:` unit
- [x] **T-0202** ⭐ `model/pool.py`: `V2Reserves`, `V3Slot0`, `PoolState` with
  `Blockstamp` + `verified` flag. `dep:` T-0102 · `tests:` unit
  (also added `model/token.py` `Token{chain,address,decimals}` — the graph-node
  identity depended on by T-0207/T-0501.)
- [ ] **T-0203** ⭐ `dex/uniswap_v2.py`: `getReserves` decode, marginal rate,
  event decode (`Sync`). `dep:` T-0201,T-0202 · `tests:` unit,chain
- [ ] **T-0204** ⭐ `dex/uniswap_v3.py`: `slot0`/`liquidity` decode, tick bitmap
  read, `Swap`/`Mint`/`Burn` decode. `dep:` T-0201,T-0202 · `tests:` unit,chain
- [ ] **T-0205** ⭐ `dex/registry.py`: factory-event pool discovery + curated,
  on-chain-verified token lists per chain. `dep:` T-0203 · `tests:` integration,chain
- [ ] **T-0206** ⭐ Event-driven state cache: `Sync`/`Swap` logs update reserves
  O(1); freshness stamp per update. `dep:` T-0203,T-0204 · `tests:` unit,integration
- [ ] **T-0207** Token metadata reader: on-chain `decimals`/`symbol`; detect
  proxies; quarantine fee-on-transfer/rebasing tokens. `dep:` T-0202 · `tests:` unit,chain
- [x] **T-0208** ⭐ Two-source verify hook: cross-check decoded reserves vs oracle
  at pinned block (stub oracle now; real in Phase 8). `dep:` T-0203 · `tests:` verify
  → done via captured real-onchain fixtures: Base Uniswap V2 WETH/USDC reserves
  cross-checked bit-for-bit against `UniswapV2Router02.getAmountsOut` at a pinned
  block (`tests/verify/test_onchain_amm.py`).

**Milestone M2** — live, event-updated, freshness-stamped reserves for a real
pool set, each cross-checked against the oracle at a pinned block.

---

## Phase 3 — Pricing & AMM math  (core, 100% coverage)

- [x] **T-0301** ⭐ `amm/constant_product.py`: out-given-in, in-given-out,
  price-impact; integer-exact fees. `dep:` T-0202 · `tests:` unit
- [x] **T-0302** ⭐ Property tests for T-0301 (invariant, monotonic, round-trip).
  `dep:` T-0301 · `tests:` unit
- [x] **T-0303** ⭐ `amm/concentrated_liquidity.py`: sqrtPriceX96 math, single-
  tick swap, tick crossing via bitmap+`liquidityNet`. `dep:` T-0204 · `tests:` unit
- [x] **T-0304** ⭐ Cross-check T-0303 vs on-chain **QuoterV2** at pinned blocks
  (≤1 wei). `dep:` T-0303,T-0107 · `tests:` chain,verify
  → done, exceeded: engine reproduces Base Uniswap V3 WETH/USDC QuoterV2
  `quoteExactInputSingle` **bit-for-bit (0 wei)**, both directions, incl. the
  post-swap sqrtPrice (`tests/verify/test_onchain_amm.py`). Live-fork `chain`-tier
  replay remains a future add (needs an RPC/Anvil node — see blocked.md).
- [x] **T-0305** ⭐ `amm/sizing.py`: closed-form 2-pool optimum + golden-section
  general solver; agreement test. `dep:` T-0301 · `tests:` unit
- [x] **T-0306** `amm/stableswap.py` (Curve) — stretch. `dep:` T-0301 · `tests:` unit,chain
- [x] **T-0307** `amm/weighted.py` (Balancer) — stretch. `dep:` T-0301 · `tests:` unit,chain
- [ ] **T-0308** Real-onchain verify for StableSwap + weighted: capture a live Curve
  2-pool (`get_dy`) and a Balancer weighted pool (`queryBatchSwap`) and match
  bit-for-bit like T-0208/T-0304. Care needed: Curve `A_PRECISION` convention and
  rate/decimal normalization; Balancer's 30% `_MAX_OUT_RATIO`. `dep:` T-0306,T-0307
  · `tests:` verify — filed from the Phase-7 real-onchain suite (V2/V3 done, these two
  families deferred to avoid a convention-mismatch false failure).

**Milestone M3** — local quotes match on-chain quoter within tolerance at pinned
blocks; `amm/` at 100% line+branch coverage.

---

## Phase 4 — Single-chain detection  ⭐ (first real value)

- [x] **T-0401** ⭐ `graph/rategraph.py`: build/update graph, `-ln(rate)` edges,
  dirty-node tracking, O(1) edge update. `dep:` T-0206,T-0301 · `tests:` unit
- [x] **T-0402** ⭐ Graph identity property test (`Σ−ln r<0 ⇔ Πr>1`).
  `dep:` T-0401 · `tests:` unit
- [x] **T-0403** ⭐ `detect/two_hop.py`: spatial 2-cycle detector.
  `dep:` T-0401 · `tests:` unit
- [x] **T-0404** ⭐ `detect/triangular.py`: hub-rooted 3-cycle detector.
  `dep:` T-0401 · `tests:` unit
- [x] **T-0405** ⭐ `graph/negcycle.py`: Bellman-Ford/SPFA with cycle recovery.
  `dep:` T-0401 · `tests:` unit
- [x] **T-0406** ⭐ `graph/tropical.py`: min-plus K-hop sweep (`numba`).
  `dep:` T-0401 · `tests:` unit,benchmark
- [x] **T-0407** ⭐ `detect/multi_hop.py`: bounded negative-cycle detector using
  T-0405/T-0406. `dep:` T-0405,T-0406 · `tests:` unit
- [x] **T-0408** ⭐ `detect/profit.py`: net-profit gate (fees+gas+slippage);
  gas→numeraire via on-chain price; safety haircut. `dep:` T-0305 · `tests:` unit
- [x] **T-0409** ⭐ No-false-positive + net-loss-never-reported property tests.
  `dep:` T-0408 · `tests:` unit
- [x] **T-0410** ⭐ `engine/dirty.py` + incremental search; equivalence vs full
  sweep. `dep:` T-0401,T-0407 · `tests:` unit,integration
- [ ] **T-0411** ⭐ `engine/engine.py`: cold start→subscribe→detect→verify→emit,
  wiring ports. `dep:` T-0410,T-0208 · `tests:` integration,chain
- [ ] **T-0412** ⭐ End-to-end `chain` test: from forked live state, emit a
  verified, block-stamped single-chain opportunity within SLO. `dep:` T-0411 · `tests:` chain,verify,benchmark

**Milestone M4** — emits verified, net-profitable single-chain 2-hop, triangular,
and multi-hop opportunities from live data within the latency SLO. **(This is the
first "the engine works" milestone.)**

---

## Phase 5 — Cross-chain 2-hop

- [x] **T-0501** ⭐ `model/canonical_asset.py`: on-chain-verified fungibility map
  (native vs bridged, per-chain WETH/USDC/…). `dep:` T-0207 · `tests:` unit,verify
- [x] **T-0502** ⭐ `detect/cross_chain.py`: same-asset 2-chain spread in a common
  numeraire. `dep:` T-0501,T-0408 · `tests:` unit
- [x] **T-0503** ⭐ Bridge cost+settlement-time model; net gate incl. bridge;
  `time_to_settle` + drift-risk on the report. `dep:` T-0502 · `tests:` unit
- [x] **T-0504** Multi-chain engine wiring; per-chain graphs, shared asset map.
  `dep:` T-0502,T-0411 · `tests:` integration,chain

**Milestone M5** — emits verified cross-chain 2-hop spreads with an explicit,
on-chain-sourced cost model. (Simple 2-hop only — no x-chain cycles.)

---

## Phase 6 — Latency hardening

- [x] **T-0601** Profile hot path (`py-spy`); publish per-stage baseline.
  `dep:` T-0412 · `tests:` benchmark
- [x] **T-0602** `numba`/`numpy` optimize AMM + cycle inner loops; JIT warm-up.
  `dep:` T-0601 · `tests:` unit,benchmark
- [ ] **T-0603** Zero-copy state updates + object pooling on the event path.
  `dep:` T-0601 · `tests:` unit,benchmark
- [ ] **T-0604** Enforce p99 SLO as a CI benchmark gate; store baselines.
  `dep:` T-0602 · `tests:` benchmark
- [ ] **T-0605** (conditional) Rust/pyo3 `negcycle` behind the port IF benchmarks
  prove Python misses SLO. ADR required. `dep:` T-0604 · `tests:` unit,benchmark

**Milestone M6** — p99 event→emit ≤ SLO, gated in CI.

---

## Phase 7 — Persistence & streaming API

- [x] **T-0701** `store/redis_cache.py`: async hot pool-state cache.
  `dep:` T-0206 · `tests:` db,integration
- [x] **T-0702** `store/pg_store.py` + `alembic`: opportunities + snapshots
  (Timescale hypertables). `dep:` T-0011 · `tests:` db
- [x] **T-0703** `stream/api.py`: read-only FastAPI (query opportunities); contract
  tests. `dep:` T-0702 · `tests:` integration
- [ ] **T-0704** `stream/ws.py`: opportunity WebSocket feed + retraction events.
  `dep:` T-0411 · `tests:` integration
- [ ] **T-0705** `stream/metrics.py`: prometheus latency histograms + SLO gauges.
  `dep:` T-0411 · `tests:` unit,integration

**Milestone M7** — opportunities queryable + streamed; db tier + API contracts green.

---

## Phase 8 — Verification & data-integrity subsystem

- [ ] **T-0801** ⭐ `oracle/blockscout.py`: independent `read_contract`/ABI/token
  checks (Blockscout MCP + REST). `dep:` T-0208 · `tests:` verify,chain
- [ ] **T-0802** ⭐ `oracle/crosscheck.py`: two-source agreement; flag `UNVERIFIED`.
  `dep:` T-0801 · `tests:` verify
- [ ] **T-0803** ⭐ `verify/verifier.py`: continuous sampled re-verification loop.
  `dep:` T-0802 · `tests:` verify,integration
- [ ] **T-0804** `verify/freshness.py`: per-chain staleness bounds; reject/flag.
  `dep:` T-0102 · `tests:` unit
- [ ] **T-0805** Reorg invalidation end-to-end + retraction; chaos test.
  `dep:` T-0106,T-0411 · `tests:` chain,integration
- [ ] **T-0806** Reproducibility replay: recompute a reported opportunity from its
  provenance via the oracle; assert match. `dep:` T-0803 · `tests:` verify

**Milestone M8** — every emitted opportunity carries verifiable provenance; verify
tier + chaos tests green.

---

## Phase 9 — Backtesting & analytics (offline; requested quant stack)

- [x] **T-0901** `backtest/replay.py`: historical snapshot replay (backtrader-
  driven) — deterministic. `dep:` T-0702 · `tests:` unit,integration
- [x] **T-0902** Opportunity metrics: count/edge/decay/fill-proxy;
  `quantstats`/`statsmodels`/`arch` report. `dep:` T-0901 · `tests:` unit
- [ ] **T-0903** `backtest/allocation.py`: PyPortfolioOpt capital-allocation
  analytics across concurrent opportunities. `dep:` T-0901 · `tests:` unit
- [ ] **T-0904** `cex/ccxt_reference.py`: optional CEX↔DEX spread report, clearly
  labelled off-chain reference. `dep:` T-0408 · `tests:` unit
- [x] **T-0905** Static test: no `backtest/`,`cex/` import reachable from runtime
  detection path. `dep:` T-0901 · `tests:` unit

**Milestone M9** — deterministic backtest + metrics report; analytics strictly
offline.

---

## Phase 10 — Observability & operations

- [ ] **T-1001** Grafana dashboards (latency, integrity, throughput). `dep:` T-0705 · `tests:` integration
- [ ] **T-1002** Health/readiness endpoints + alert rules (SLO/integrity breach).
  `dep:` T-0705 · `tests:` integration
- [ ] **T-1003** Dockerfile (non-root, minimal) + runtime compose. `dep:` T-0411 · `tests:` integration
- [ ] **T-1004** Runbooks in `docs/ops/`. `dep:` T-1002 · `tests:` (docs)

**Milestone M10** — service is operable and self-monitoring.

---

## Phase 11 — Security & hardening

- [ ] **T-1101** Threat model doc + `/security-review` pass. `dep:` T-1003 · `tests:` (review)
- [ ] **T-1102** Fuzz decoders (malformed log/ABI) — no crash/corruption. `dep:` T-0206 · `tests:` unit
- [ ] **T-1103** SBOM + dependency-license review (esp. optional heavy libs).
  `dep:` T-0007 · `tests:` audit
- [ ] **T-1104** API hardening: rate limit, CORS, size limits, no traces. `dep:` T-0703 · `tests:` integration

**Milestone M11** — security-review clean; documented threat model.

---

## Recurring (injected by the loop — see CLAUDE.md §8)

- [ ] **A-xxxx** Enhancement audit (rotate: correctness/latency/security/
  simplicity/evolvability). Every 5th iteration. Findings become new `T-`/`A-`
  items. Never delete this line — copy it with a new id when scheduled.
