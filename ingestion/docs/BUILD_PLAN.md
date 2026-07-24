# Build Plan — milestones the Ralph loop walks through

This is the **roadmap**. The Ralph loop (`ralph/PROMPT.md`) reads this, finds the
first milestone whose exit criteria aren't all met, does **the single next unmet
task** inside it, proves it with tests, updates `ralph/PROGRESS.md`, commits, and
stops. Next iteration repeats. The component is done when every milestone's exit
criteria pass and the loop writes `RALPH-COMPLETE`.

**Principles that keep the bug count low:**
- **One task per iteration.** Small diffs, small blast radius.
- **Every milestone leaves the build green.** Never commit a red Tier-A suite.
- **Dependencies flow downward** (`core → amm → rpc/chains → ingest/v4 →
  aggregator → engine-client → output → app`). Build in this order so each layer
  stands on a tested one.
- **Exit criteria are tests, not opinions.** "Done" = the named tests pass.
- **No mock data, ever, to make a gate pass** (see `ralph/AGENTS.md`).

Legend: each milestone lists **Scope**, **Deliverables**, and **Exit criteria
(tests that must pass)**. Tier A = deterministic/CI-gating; Tier B =
live/nightly (see `ARCHITECTURE.md §9`).

---

## M0 — Bootstrap & the engine contract in code
**Scope:** Make the repo build, lint, and test; encode the engine JSON contract
exactly, with golden serialization.
**Deliverables:**
- Rust workspace (`Cargo.toml`), `crates/core`, CI workflow (`fmt`, `clippy -D
  warnings`, `test`), `rust-toolchain.toml`.
- `core`: `DetectRequest`, `ChainContext`, `Pool` (`v2`/`v3` variants), `Token`,
  `Blockstamp`, `DetectResponse`, `Opportunity`, `Leg` — matching
  `reference/INTEGRATION.md` field-for-field.
- `U256`/`I256` ⇄ decimal-string serde helpers.
**Exit criteria (Tier A):**
- `cargo build`, `cargo clippy -D warnings`, `cargo fmt --check` clean.
- Golden test: serialize a hand-built `DetectRequest` → byte-equal to a checked-in
  fixture that mirrors the contract's example; and deserialize the contract's
  example response.
- Round-trip test: a `U256` ≈ 2¹⁶⁰ and a reserve ≈ 2¹¹² survive
  string→U256→string unchanged.

## M1 — RPC & transport layer
**Scope:** Talk to all five chains reliably.
**Deliverables:**
- `crates/rpc`: `alloy` WS provider (`newHeads`, `logs` subscriptions), HTTP
  provider (archive/reconcile), Multicall3 client (`0xcA11…76CA11`), a
  `ChainProvider` trait, reconnect with exponential backoff + jitter, request
  coalescing.
- `crates/chains`: per-chain static params (chain_id, block time, predeploy
  addresses, gas model tag) for the five chains.
**Exit criteria:**
- (Tier A) Unit tests: backoff schedule, subscription frame decode, multicall
  encode/decode vs fixtures; reconnect state machine via a mock transport.
- (Tier B) Live smoke: connect to each of the five endpoints, receive ≥1
  `newHead` and Multicall3 result from each; assert Multicall3 code present at the
  canonical address on each chain.

## M2 — On-chain validation gate & pool registry
**Scope:** Every configured address is proven current & correct before use.
**Deliverables:**
- `config/pools/<chain>.toml` schema + loader.
- The validation gate (`ARCHITECTURE.md §7`): code-exists, token0/1, fee,
  decimals/symbol, factory check, V4 hook gate.
- Loud, structured reject/park of invalid entries (never silent).
**Exit criteria:**
- (Tier A, pinned block/fork) For a curated registry of real pools per chain, the
  gate passes valid entries and rejects deliberately-corrupted ones (wrong token,
  wrong fee, non-contract, denied token, unsafe V4 hook).
- (Tier A) Every declared token's `decimals`/`symbol` matches the fork read.

## M3 — AMM math & native-price derivation (pure)
**Scope:** Correct pricing math, no I/O.
**Deliverables:**
- `crates/amm`: V2 `getAmountOut`/spot; V3/V4 tick math (`sqrtPriceX96`↔price,
  active-tick `liquidity`); `native_price_in[T]` derivation from a WETH/T pool
  (`ENGINE_CONTRACT.md §7`).
**Exit criteria (Tier A):**
- Known-answer vectors for V2 and V3 pricing (cross-checked against independently
  computed values / on-chain quotes at a pinned block) match within exact/defined
  tolerance.
- `proptest`: monotonicity, no panics/overflows across the full `U256` input
  range, price > 0 for valid reserves.
- `native_price_in` for USDC/WETH at a pinned block matches the pool-derived
  value; a numeraire with no price path is omitted (asserted).

## M4 — V2 ingestor
**Scope:** Live, verified constant-product pool state.
**Deliverables:**
- `crates/ingest` V2 path: `Sync` decode → mirror; startup `getReserves`
  multicall seed; blockstamping; emit `kind:"v2"` pool objects; `verified` wiring.
**Exit criteria:**
- (Tier A, pinned block/fork) **Event-derived reserves == `eth_call getReserves`
  at block N**, exactly, for every V2 pool in the fixture registry.
- (Tier A) Feeding these pool objects through `core` serialization yields valid
  contract JSON (schema + golden).

## M5 — V3 ingestor
**Scope:** Live, verified concentrated-liquidity state.
**Deliverables:**
- V3 path: `Swap`/`Mint`/`Burn` decode; `slot0()`+`liquidity()` seed;
  in-range-liquidity refresh on `Mint`/`Burn`; emit `kind:"v3"`.
**Exit criteria:**
- (Tier A, pinned block/fork) **Event-derived `sqrt_price_x96`/`tick`/`liquidity`
  == `slot0()`/`liquidity()` at block N**, exactly, for every V3 pool in the
  fixture.

## M6 — Uniswap V4 adapter (Unichain-critical)
**Scope:** Singleton V4 pools mapped onto the engine's `v3` shape.
**Deliverables:**
- `crates/v4`: `PoolManager` log subscription filtered by `poolId`; decode
  `Swap`/`ModifyLiquidity`; `StateView.getSlot0/getLiquidity` seed + reconcile;
  hook safety gate; dynamic-fee read; emit `kind:"v3"` with `poolId` identity and
  the `PoolKey` retained for downstream.
**Exit criteria:**
- (Tier A, pinned block/fork on Unichain) **V4 event-derived state ==
  `StateView` read at block N**, exactly, for the fixture V4 pools.
- (Tier A) Hook gate: a pool with a non-safe-list hook is rejected; a `0x0`-hook
  pool is accepted; a dynamic-fee pool's fee is read for the stamped block.

## M7 — Gas & price context
**Scope:** Correct per-chain economics for the engine.
**Deliverables:**
- `crates/chains` gas adapters: OP-Stack `GasPriceOracle.getL1Fee` (Base, OP,
  Unichain, Ink) for `l1_data_fee_wei`; `eth_gasPrice`/feeHistory for
  `gas_price_wei`; Arbitrum `ArbGasInfo` path (or `l1_data_fee_wei:0` + config
  multiplier). Assemble the full `ChainContext` incl. `native_price_in`, `hubs`.
**Exit criteria:**
- (Tier A, pinned block/fork) `gas_price_wei` and `l1_data_fee_wei` match the
  oracle/precompile reads at block N within defined tolerance for each chain.
- (Tier A) `ChainContext` omits any numeraire lacking a native-price path and logs
  it.

## M8 — Aggregator & engine client
**Scope:** Assemble synchronized snapshots and talk to the real engine.
**Deliverables:**
- `crates/aggregator`: atomic per-chain snapshots (sync policy,
  `ENGINE_CONTRACT.md §5`); `DetectRequest` builder; cadence/debounce
  (`on_change` + heartbeat); `incremental` mode after first request; `cross_chain`
  wiring from config.
- `crates/engine-client`: `EngineClient` trait; `reqwest` keep-alive HTTP impl
  (`POST /detect`, `GET /health`); subprocess impl; timeout/retry/backpressure;
  response validation.
**Exit criteria:**
- (Tier A) Snapshot invariants (`proptest`): one block per chain per request;
  `incremental` sends only changed pools; first request is `incremental:false`.
- (Tier A) **Contract-with-engine**: real `l2arb` (`pip install`ed) returns a
  schema-valid `DetectResponse` with `net_profit>0` for a fixture snapshot known
  to contain an arb; blockstamps round-trip; a malformed request yields the
  documented error shape.

## M9 — Output sink, reorg, reconcile, observability
**Scope:** Fan-out, resilience, and eyes.
**Deliverables:**
- `crates/output`: sink trait + WS server (default), NDJSON stdout, Redis, gRPC;
  versioned envelope (`ARCHITECTURE.md §10`).
- Reorg rollback + `verified:false` transitions (`§6`); background reconciliation
  loop; `crates/observability`: latency histograms, `/health`, Prometheus
  `/metrics`, `tracing`.
**Exit criteria:**
- (Tier A) Reorg test: injected conflicting parent hash → mirror rolls back,
  affected pools go `verified:false`, then recover; no stale emission.
- (Tier A) Reconcile test: injected mismatch flips `verified:false` and re-seeds.
- (Tier A) Output envelope schema test; a subscriber receives a well-formed
  snapshot + opportunities message.

## M10 — Hardening, live proof, and ship
**Scope:** Prove it's fast, real, and plug-and-play; then declare done.
**Deliverables:**
- `benches/` criterion hot-path benchmarks with CI-asserted budgets.
- `scripts/` live soak + on-chain sampling harness.
- End-to-end from `config.example.toml`.
- Optional Flashblocks pre-confirmation path (Base/Unichain), config-gated,
  `verified:false` until full-block confirm.
- README quickstart, integration docs, `Dockerfile`, graceful shutdown, `SIGHUP`
  reload.
**Exit criteria:**
- (Tier A) All prior milestones' Tier-A suites green; benches run and record p50/p99.
- (Tier B) Hot-path p99 within the `ARCHITECTURE.md §8` budget; **live soak:
  reconciliation 100% over the window, zero memory growth, reconnect exercised,
  sampled snapshots re-verified on-chain**; e2e plug-and-play run emits
  opportunities within budget against live endpoints + live `l2arb`.
- When **all** of the above hold, append the `RALPH-COMPLETE` sentinel to
  `ralph/PROGRESS.md` with a one-line evidence summary (commit SHAs / bench
  numbers / soak duration).

---

## Cross-cutting acceptance (true throughout, re-checked at M10)
- **No mock/synthetic on-chain data** anywhere in the shipped path; fixtures are
  *recorded real* on-chain data at pinned blocks.
- **`verified` is never faked.**
- **Tier A is 100% green on the committed HEAD.**
- **Every configured address is validated on-chain** (M2 gate) before use.
- **The component builds and runs from `config.example.toml`** with only
  endpoints filled in.
