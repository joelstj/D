# Ralph backlog — the ordered build list

This is the **source of truth** for what the loop builds next. Work top-to-bottom. Pick the highest
task that is unchecked (`- [ ]`) and whose `deps` are all checked. Check off with `- [x]` when
`scripts/verify.sh` is GREEN and the task's *done* condition holds. Add discovered tasks in place.

Task id = `P{phase}-T{n}`. Phases mirror `docs/BUILD_PLAN.md`. `spec:` points at the authority to read.

Legend: `deps:` prerequisite task ids · `spec:` doc to read first · `(BLOCKED: …)` needs a human.

---

## Phase 0 — Baseline & tooling  ·  _exit: green CI on all 5 chains' build profiles_

- [ ] **P0-T1** Bootstrap toolchain and confirm/repair the green baseline: run `scripts/bootstrap.sh`,
  then `scripts/verify.sh`; fix any compile/test/fmt issue in the skeleton until GREEN. — _deps: none_ — _spec: README.md, CLAUDE.md_
- [ ] **P0-T2** Add CI (GitHub Actions) that runs `scripts/verify.sh` on push/PR, plus a matrix build
  under `FOUNDRY_PROFILE=shanghai` and `=default`. — _deps: P0-T1_ — _spec: docs/BUILD_PLAN.md#phase-0_
- [ ] **P0-T3** Add `forge fmt` + `forge snapshot` gates and commit an initial `.gas-snapshot`. — _deps: P0-T1_ — _spec: docs/specs/07-gas-and-yul.md_
- [ ] **P0-T4** Add the config JSON schema validator to `verify.sh` (validate `config/chains/*.json`
  against `config/chains/schema.json`). — _deps: P0-T1_ — _spec: config/chains/README.md_
- [ ] **P0-T5** Wire the address-verification report: a script that lists every `"_verify": true` /
  zero-address entry so research mode has a worklist. — _deps: P0-T4_ — _spec: docs/specs/01-chains.md_

## Phase 1 — Chain & protocol registry  ·  _exit: every shipped address verified & sourced_

- [ ] **P1-T1** Verify chain params for all five chains (chainId, block time, EVM version support,
  gas model, canonical bridge) and record in `config/chains/*.json` + `docs/specs/01-chains.md`. — _deps: P0-T5_ — _spec: docs/specs/01-chains.md_
- [ ] **P1-T2** Verify OP-Stack predeploys (WETH `0x42..06`, `L1Block`, `GasPriceOracle`) per OP chain. — _deps: P1-T1_ — _spec: docs/specs/01-chains.md_
- [ ] **P1-T3** Verify Arbitrum precompiles used (`ArbGasInfo`, `ArbSys`) and its L1 fee accounting. — _deps: P1-T1_ — _spec: docs/specs/01-chains.md_
- [ ] **P1-T4** Verify & fill the **flash-loan provider availability matrix** per chain (Aave V3,
  Balancer V2 Vault, Uniswap V3 flash, Uniswap V4). Flag absent ones. — _deps: P1-T1_ — _spec: docs/specs/02-flash-loans.md_
- [ ] **P1-T5** Verify & fill the **DEX registry** per chain (factory/router/quoter/PoolManager
  addresses, fee tiers, families present). — _deps: P1-T1_ — _spec: docs/specs/03-dex-adapters.md_
- [ ] **P1-T6** Verify core token addresses per chain (WETH/WokenGas, USDC/USDbC, USDT, DAI, and each
  chain's canonical stables) with decimals. — _deps: P1-T1_ — _spec: config/chains/README.md_
- [ ] **P1-T7** Build a typed config loader (Solidity `test` helper + off-chain) that reads the
  registries so nothing hardcodes addresses. — _deps: P1-T5_ — _spec: docs/specs/11-offchain-and-sdk.md_

## Phase 2 — Core executor & route codec  ·  _exit: executor dispatches a mocked 2-hop route atomically_

- [ ] **P2-T1** Specify & implement the **RouteCodec** compact byte layout (Solidity + Yul decoder)
  with a differential fuzz test vs a reference decoder. — _deps: P0-T1_ — _spec: docs/specs/09-route-codec.md_
- [ ] **P2-T2** Implement `ArbExecutor` access control (owner + operator allowlist) and pausable
  circuit breaker with events + tests. — _deps: P2-T1_ — _spec: docs/specs/08-security.md_
- [ ] **P2-T3** Implement the reentrancy guard (transient storage on Cancun, storage fallback on
  shanghai) with an invariant test. — _deps: P2-T2_ — _spec: docs/specs/07-gas-and-yul.md, 08-security.md_
- [ ] **P2-T4** Implement the executor entrypoint `execute(bytes route, ...)`: decode route, dispatch
  hops through the adapter interface, enforce the **profit invariant** and `minProfit`, sweep proceeds.
  Use mock adapters. — _deps: P2-T1, P2-T3_ — _spec: docs/specs/04-strategies.md, 08-security.md_
- [ ] **P2-T5** Implement the generic flash-callback router that validates caller+initiator and resumes
  the route. Test against a malicious-caller mock. — _deps: P2-T4_ — _spec: docs/specs/02-flash-loans.md_
- [ ] **P2-T6** Add `IDexAdapter` and `IFlashProvider` interfaces + a `MockAdapter`/`MockPool` test kit. — _deps: P2-T1_ — _spec: docs/specs/03-dex-adapters.md_
- [ ] **P2-T7** Invariant test suite: no path leaves the vault with fewer profit-token than it started;
  no unexpected token balance change; guard never re-enters. — _deps: P2-T4, P2-T5_ — _spec: docs/specs/08-security.md_

## Phase 3 — Flash-loan provider adapters  ·  _exit: each available provider round-trips on a fork_

- [ ] **P3-T1** Aave V3 `flashLoanSimple` adapter + fork test on Optimism/Base/Arbitrum. — _deps: P2-T5, P1-T4_ — _spec: docs/specs/02-flash-loans.md_
- [ ] **P3-T2** Balancer V2 Vault flash-loan adapter (0-fee) + fork test where available. — _deps: P2-T5, P1-T4_ — _spec: docs/specs/02-flash-loans.md_
- [ ] **P3-T3** Uniswap V3 `pool.flash` adapter + fork test. — _deps: P2-T5, P1-T5_ — _spec: docs/specs/02-flash-loans.md_
- [ ] **P3-T4** Uniswap V4 flash-accounting (`PoolManager.unlock`) borrow adapter + fork test (Unichain). — _deps: P2-T5, P1-T5_ — _spec: docs/specs/02-flash-loans.md, 03-dex-adapters.md_
- [ ] **P3-T5** DEX-native flash-swap path (borrow directly inside the first hop) + fork test. — _deps: P3-T3_ — _spec: docs/specs/02-flash-loans.md_
- [ ] **P3-T6** Provider-selection library: pick the cheapest available provider per (chain, token,
  amount) using the registry + fee model. — _deps: P3-T1, P3-T2, P3-T3_ — _spec: docs/specs/02-flash-loans.md_

## Phase 4 — DEX adapters  ·  _exit: each adapter matches on-chain quotes within tolerance on a fork_

- [ ] **P4-T1** Uniswap V2 / Solidly constant-product adapter (getReserves, swap, fee variants) + fork
  tests (Velodrome/Aerodrome vAMM/sAMM, Camelot V2). — _deps: P2-T6_ — _spec: docs/specs/03-dex-adapters.md_
- [ ] **P4-T2** Uniswap V3 exactInput adapter (single + multi-tick) + quote parity fork test. — _deps: P2-T6_ — _spec: docs/specs/03-dex-adapters.md_
- [ ] **P4-T3** Uniswap V4 swap adapter via `PoolManager` + hooks awareness + fork test (Unichain/Base). — _deps: P2-T6, P3-T4_ — _spec: docs/specs/03-dex-adapters.md_
- [ ] **P4-T4** Solidly-CL (Velodrome/Aerodrome Slipstream) adapter + fork test. — _deps: P4-T2_ — _spec: docs/specs/03-dex-adapters.md_
- [ ] **P4-T5** Algebra (Camelot V3) dynamic-fee adapter + fork test. — _deps: P4-T2_ — _spec: docs/specs/03-dex-adapters.md_
- [ ] **P4-T6** Curve StableSwap adapter (`exchange`/`get_dy`) + fork test. — _deps: P2-T6_ — _spec: docs/specs/03-dex-adapters.md_
- [ ] **P4-T7** Balancer V2 batchSwap adapter + fork test. — _deps: P2-T6_ — _spec: docs/specs/03-dex-adapters.md_
- [ ] **P4-T8** (Arbitrum) TraderJoe Liquidity Book adapter + fork test. — _deps: P2-T6_ — _spec: docs/specs/03-dex-adapters.md_

## Phase 5 — Strategies  ·  _exit: 2-hop, triangular, cross-DEX routes execute on forks_

- [ ] **P5-T1** Same-chain **2-hop** cross-DEX strategy + route builder + fork test (real pools). — _deps: P4-T2, P3-T6_ — _spec: docs/specs/04-strategies.md_
- [ ] **P5-T2** Same-chain **triangular** (3-leg) strategy + route builder + fork test. — _deps: P5-T1_ — _spec: docs/specs/04-strategies.md_
- [ ] **P5-T3** Mixed-family routing (e.g. V3→Curve→V2) validated end-to-end. — _deps: P5-T2, P4-T6_ — _spec: docs/specs/04-strategies.md_
- [ ] **P5-T4** Negative tests: unprofitable route reverts; stale/again-priced pool reverts within
  `minProfit`/deadline. — _deps: P5-T1_ — _spec: docs/specs/08-security.md_

## Phase 6 — Dynamic loan sizing  ·  _exit: sized trade stays under price-impact bound & maximizes net profit_

- [ ] **P6-T1** On-chain sizing guards: enforce per-hop `minOut`, aggregate `minProfit`, and a max
  price-impact bound; revert on breach. — _deps: P5-T1_ — _spec: docs/specs/05-loan-sizing.md_
- [ ] **P6-T2** Off-chain CPMM closed-form optimal-input solver for 2-pool arb (+ fee) with tests vs a
  numerical reference. — _deps: P5-T1_ — _spec: docs/specs/05-loan-sizing.md_
- [ ] **P6-T3** Concentrated-liquidity depth model: cap input so price stays within a sqrtPrice bound
  given active-tick liquidity. — _deps: P6-T2, P4-T2_ — _spec: docs/specs/05-loan-sizing.md_
- [ ] **P6-T4** Generic ternary-search sizer over the realized-profit function for arbitrary curves. — _deps: P6-T2_ — _spec: docs/specs/05-loan-sizing.md_
- [ ] **P6-T5** Gas-adjusted breakeven & final size = `min(profit-optimal, depth-bounded,
  flash-liquidity, gas-breakeven)`; wire into the route builder. — _deps: P6-T2, P6-T3, P3-T6_ — _spec: docs/specs/05-loan-sizing.md_
- [ ] **P6-T6** Liquidity-scaled sizing property tests: size increases with depth, decreases with
  thinness, never exceeds the impact bound (fuzz). — _deps: P6-T5_ — _spec: docs/specs/05-loan-sizing.md_

## Phase 7 — MEV, ordering & gas  ·  _exit: benchmarked hot-path gas + submission strategy per chain_

- [ ] **P7-T1** Priority-fee / tip module: per-chain tip strategy incl. OP-Stack fee split and a value
  cap tied to expected profit. — _deps: P6-T5_ — _spec: docs/specs/06-mev-and-ordering.md_
- [ ] **P7-T2** Private / sequencer-direct submission adapter and backrun-bundle formatting. — _deps: P7-T1_ — _spec: docs/specs/06-mev-and-ordering.md_
- [ ] **P7-T3** Arbitrum **Timeboost** express-lane support (bid/submit path) behind a flag. — _deps: P7-T1_ — _spec: docs/specs/06-mev-and-ordering.md_
- [ ] **P7-T4** Yul optimization pass on the hot path (route decode, calldata packing, low-level calls)
  with differential tests + gas-snapshot deltas recorded. — _deps: P5-T3_ — _spec: docs/specs/07-gas-and-yul.md_
- [ ] **P7-T5** Deadline + block-tag freshness guards to neutralize stale-bundle inclusion. — _deps: P7-T1_ — _spec: docs/specs/06-mev-and-ordering.md_

## Phase 8 — Off-chain engine & multi-language SDKs  ·  _exit: any language can find, simulate, fire_

- [ ] **P8-T1** Language-agnostic route-encoder reference + conformance vectors (JSON fixtures) that
  every SDK must reproduce. — _deps: P2-T1_ — _spec: docs/specs/09-route-codec.md_
- [ ] **P8-T2** TypeScript SDK: config loader, route encoder, quoter, `eth_call` simulator, submitter. — _deps: P8-T1_ — _spec: docs/specs/11-offchain-and-sdk.md_
- [ ] **P8-T3** Python SDK (parity with TS via the conformance vectors). — _deps: P8-T1_ — _spec: docs/specs/11-offchain-and-sdk.md_
- [ ] **P8-T4** Rust SDK (parity). — _deps: P8-T1_ — _spec: docs/specs/11-offchain-and-sdk.md_
- [ ] **P8-T5** Go SDK (parity). — _deps: P8-T1_ — _spec: docs/specs/11-offchain-and-sdk.md_
- [ ] **P8-T6** Opportunity scanner: multi-DEX price watch → candidate routes (per chain). — _deps: P8-T2_ — _spec: docs/specs/11-offchain-and-sdk.md_
- [ ] **P8-T7** Simulation gate: fork/`eth_call` dry-run that rejects any route not net-profitable
  before submission. — _deps: P8-T6, P6-T5_ — _spec: docs/specs/11-offchain-and-sdk.md_
- [ ] **P8-T8** Language-neutral gateway (REST + gRPC) wrapping encode/quote/simulate/submit for apps
  that aren't in a first-class SDK language. — _deps: P8-T2_ — _spec: docs/INTEGRATION.md_

## Phase 9 — Cross-chain 2-hop  ·  _exit: non-atomic settlement modeled, hedged, and accounted_

- [ ] **P9-T1** Cross-chain design note: why atomicity is impossible; inventory vs bridge-intent
  models; risk & hedging. (spec first, no contract yet) — _deps: P5-T1_ — _spec: docs/specs/10-cross-chain.md_
- [ ] **P9-T2** Inventory-based settlement contracts on each leg (deposit/claim/refund) + tests. — _deps: P9-T1_ — _spec: docs/specs/10-cross-chain.md_
- [ ] **P9-T3** Bridge/intent adapter interface (e.g. fast-bridge / solver network) behind a common
  API; mock + one real adapter on a fork. — _deps: P9-T2_ — _spec: docs/specs/10-cross-chain.md_
- [ ] **P9-T4** Cross-chain orchestrator (off-chain) with inventory accounting, price hedging, and a
  hard per-route exposure cap `(BLOCKED: human sets exposure caps)`. — _deps: P9-T3_ — _spec: docs/specs/10-cross-chain.md_

## Phase 10 — Hardening, deployment & release  ·  _exit: audit-ready, deterministic deploys, runbooks_

- [ ] **P10-T1** Echidna/Foundry invariant campaign on the executor + sizing; document properties. — _deps: P6-T6, P2-T7_ — _spec: docs/specs/08-security.md_
- [ ] **P10-T2** Slither/Semgrep clean pass; triage & justify every remaining finding. — _deps: P5-T3_ — _spec: docs/specs/08-security.md_
- [ ] **P10-T3** Halmos/Certora symbolic proof of the profit invariant and route-codec round-trip. — _deps: P2-T7_ — _spec: docs/specs/08-security.md_
- [ ] **P10-T4** Deterministic CREATE2 deployment scripts for address parity across all five chains
  (no `--broadcast` in the loop). — _deps: P5-T3_ — _spec: docs/specs/01-chains.md_
- [ ] **P10-T5** Operator runbook + monitoring/alerting spec (profit, revert-rate, inventory drift). — _deps: P8-T7_ — _spec: docs/INTEGRATION.md_
- [ ] **P10-T6** External audit prep packet (scope, invariants, threat model, known limitations)
  `(BLOCKED: human commissions audit before any mainnet capital)`. — _deps: P10-T1, P10-T2, P10-T3_ — _spec: docs/specs/08-security.md_

---

### Adding tasks
When you discover work mid-iteration, add it under the right phase with the next free id and a `deps:`
line. Keep it iteration-sized (completable + verifiable in one loop). Split anything bigger.
