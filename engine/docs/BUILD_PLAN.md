# l2arb — Master Build Plan

> The authoritative narrative of *what we are building and in what order*. The
> executable, tick-as-you-go version of this plan is `plan/backlog.md`; the
> acceptance bars are `plan/milestones.md`. This document explains the shape and
> the reasoning so any single Ralph iteration can understand where its one task
> fits.

## 0. One-paragraph summary

`l2arb` ingests **live Layer-2 blockchain state** (DEX pool reserves, ticks,
liquidity) over low-latency RPC/WebSocket subscriptions, maintains an in-memory
**token exchange-rate graph** per chain, and continuously searches that graph for
profitable cycles — **2-hop, triangular, and bounded multi-hop** on a single
chain, plus **simple 2-hop spreads across chains**. Every opportunity is priced
net of fees, gas, and slippage, is stamped with the exact block it was derived
from, and is **independently verified against on-chain state** before it is
trusted. It is **detection-only**: it never holds keys and never trades.

## 1. Design tenets (why the system looks the way it does)

1. **On-chain truth or nothing.** The only acceptable input is state that can be
   re-derived from a specific block and confirmed by an independent oracle. This
   drives the adapter design, the freshness metadata on every quote, and the
   entire `verify` test tier. (`docs/DATA_INTEGRITY.md`)
2. **Latency is a feature, measured and gated.** "Near-zero-latency" is made
   concrete as a per-stage budget with p99 SLOs enforced by benchmark tests in
   CI. We optimize the *event→opportunity* path and treat regressions as build
   failures. (`docs/LATENCY.md`)
3. **Correct math, provably.** AMM pricing and cycle detection are pure,
   deterministic, and property-tested against invariants and against real
   on-chain quoter results. (`docs/ARBITRAGE_THEORY.md`)
4. **Add-an-adapter evolvability.** New chains and DEX families slot in behind
   typed ports; the detection core never imports web3. (`docs/ARCHITECTURE.md`)
5. **Test-first, always green.** Nothing is done without tests across the
   relevant tiers, and the tree is never committed red. (`docs/TESTING_STRATEGY.md`)

## 2. Phased plan

Each phase produces a working, tested vertical slice. Phases are ordered so the
engine is *demonstrably real* as early as possible: by end of Phase 4 it detects
true single-chain opportunities from live data. Full task breakdown with ids and
acceptance criteria is in `plan/backlog.md`.

### Phase 0 — Foundation & tooling  *(bootstraps the loop)*
Repo scaffold, `uv`/`ruff`/`mypy`/`pytest`, coverage & pairing gates, pre-commit,
CI, config (`pydantic-settings`), structured logging, the test-pairing and
no-secrets guards, and a first green smoke test. **Exit:** `make ci` green on an
empty-but-real project; the Ralph loop can run start-to-commit.

### Phase 1 — Chain connectivity layer
Async multi-endpoint RPC client (HTTP + WSS) with retry/backoff, failover, rate
limiting, `newHeads`/`logs` subscriptions, reconnection, and reorg-aware block
tracking. Freshness metadata (block number + timestamp) attached to every read.
**Exit:** can subscribe to a live L2, track head, and survive an endpoint drop;
`chain`-tier tests pass against an Anvil fork.

### Phase 2 — DEX adapters & pool state
Typed `Pool`/`DexAdapter` ports. Adapters for **Uniswap-V2-style** (`getReserves`)
and **Uniswap-V3-style** (`slot0`, `liquidity`, tick bitmap). Pool discovery via
factory events + curated token lists. Event-driven state cache: `Sync`/`Swap`
logs update reserves in O(1) instead of re-fetching. **Exit:** live reserves for
a set of real pools, kept current from logs, each verified against the oracle.

### Phase 3 — Pricing & AMM math
Pure math modules: constant-product out-given-in & price impact; V3 concentrated
-liquidity swap math with tick crossing; fee tiers; (later) Curve StableSwap and
Balancer weighted. Optimal-trade-size solver (closed form where available,
golden-section otherwise). Property tests + cross-check vs on-chain QuoterV2.
**Exit:** local quotes match on-chain quoter within tolerance at pinned blocks.

### Phase 4 — Single-chain opportunity detection  *(first real value)*
Token exchange-rate graph (`-ln(rate)` edges). Detectors: 2-hop spatial,
triangular, and bounded multi-hop **negative-cycle** search (Bellman-Ford/SPFA +
tropical-matrix K-hop). Incremental re-search over the subgraph dirtied by each
block. Net-profit model (fees + gas + slippage) and size optimization. **Exit:**
emits verified, profitable single-chain opportunities from live data, within the
latency SLO, with a reproducible block stamp.

### Phase 5 — Cross-chain 2-hop detection
Canonical asset identity map across chains (native vs bridged, e.g. USDC vs
USDC.e). Same-asset spread detection across two chains in a common numeraire,
net of a **bridge cost + settlement-time** model, flagged with time-to-settle
risk. **Simple 2-hop only.** **Exit:** emits verified cross-chain spreads with an
explicit, on-chain-sourced cost model.

### Phase 6 — Latency hardening
Profile and squeeze the hot path: `numba`/`numpy` inner loops, `multicall` batch
loads, zero-copy cache updates, orjson WS decode. End-to-end budget enforced by
benchmark gates. Optional native (Rust/pyo3) cycle-search spike if Python can't
hold the SLO — behind the same port, opt-in. **Exit:** p99 SLO met and gated.

### Phase 7 — Persistence & streaming API
Redis hot-state cache; Postgres/TimescaleDB store for opportunities + pool
snapshots (with `alembic` migrations); a **read-only** FastAPI + WebSocket feed
of detected opportunities. **Exit:** opportunities are queryable and streamed;
db-tier tests round-trip; API contract tests pass.

### Phase 8 — Verification & data-integrity subsystem
Continuous verifier that re-derives sampled pools from a pinned block and asserts
agreement with the independent oracle; reorg invalidation; staleness guards;
chaos tests (drop RPC, inject reorg). **Exit:** `verify` tier and chaos tests
green; every emitted opportunity carries a verifiable provenance record.

### Phase 9 — Backtesting & analytics *(the requested quant stack)*
Historical replay of pool snapshots; opportunity backtest (count, edge, decay,
fill-probability proxy). Integrate **backtrader** for event-driven replay,
**PyPortfolioOpt** for capital-allocation analytics across concurrent
opportunities, **quantstats**/**statsmodels**/**arch** for reporting, **CCXT**
for CEX reference spreads. **Exit:** deterministic backtest with a metrics report;
analytics are strictly offline and never feed the runtime.

### Phase 10 — Observability & operations
Prometheus metrics, Grafana dashboards, health/readiness, alerting on
SLO/data-integrity breaches, Dockerization, runbooks. **Exit:** the service is
operable and self-monitoring.

### Phase 11 — Security & hardening
Dependency & code audit (`pip-audit`, `bandit`), input-validation review, secret
-scan in CI, threat model, SBOM. **Exit:** `security-review` clean; documented
threat model.

## 3. How this plan is consumed by the Ralph loop

- `plan/backlog.md` is the ordered checklist. Each iteration does **one** item.
- Items are sized to fit one fresh-context iteration and carry explicit
  acceptance criteria and the test tiers they must satisfy.
- Milestones (`plan/milestones.md`) gate phase transitions: the loop should not
  start Phase N+1 tasks while Phase N acceptance criteria are unmet, unless a
  task is explicitly marked parallelizable.
- Enhancement audits (CLAUDE.md §8) are injected every 5th iteration and may add
  new backlog items ahead of remaining feature work when they find real issues.

## 4. Definition of "the engine works"

All true simultaneously, each backed by a green test tier:
1. From a cold start it subscribes to ≥2 live L2s and tracks head within SLO.
2. It maintains verified, fresh state for a configured pool set.
3. It emits single-chain 2-hop, triangular, and multi-hop opportunities, and
   cross-chain 2-hop spreads, each **net-profitable** and **block-stamped**.
4. Every emitted opportunity passes independent on-chain re-verification.
5. p99 event→emit latency ≤ the configured SLO, enforced in CI.
6. `make ci` is green; coverage floor is met; no secrets; no execution path.
