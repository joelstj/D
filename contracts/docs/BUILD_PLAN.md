# Build Plan — L2 Flash-Loan Arbitrage Engine

This is the master roadmap. It is executed **one task at a time** by the Ralph loop (`ralph/`), whose
ordered worklist (`ralph/BACKLOG.md`) mirrors the phases below by id (`P{phase}-T{n}`). Deep technical
detail lives in `docs/specs/`. Read this for the *shape* of the system and *why the order is this order*.

## Objectives (what "done" means)

A production-grade, **plug-and-play** arbitrage component for Optimism, Base, Ink, Unichain, and
Arbitrum One that:

1. Executes **atomic, revert-on-loss** flash-loan arbitrage — same-chain 2-hop, triangular, and
   cross-DEX — plus a non-atomic **cross-chain 2-hop** model.
2. **Sizes loans dynamically** to pool depth so trades maximize net profit without self-inflicted
   slippage/price-deviation failures.
3. Is **fast and cheap**: Solidity + Yul hot path, compact route encoding, transient storage where
   available, per-chain gas/tip modeling.
4. Is **MEV-resistant**: atomicity as the core defense, private/sequencer-direct submission, backrun
   bundling, priority-fee tuning, Arbitrum Timeboost.
5. Is **secure and robust**: enforced profit invariant, validated callbacks, fuzz + invariant +
   symbolic testing, static analysis, audit-ready.
6. Is **integrable from anything**: one stable entrypoint, a language-agnostic route encoding, SDKs in
   TypeScript/Python/Rust/Go, and a REST/gRPC gateway.

## Design principles

- **Atomicity is the product.** The unit of execution is a single transaction that either nets a
  profit ≥ the caller's minimum or reverts entirely. No partial fills, ever. This is simultaneously the
  correctness guarantee and the primary MEV defense.
- **The executor is a thin, hardened dispatcher.** All protocol-specific logic lives behind typed
  adapters (`IDexAdapter`, `IFlashProvider`). The core contract validates, routes, and enforces
  invariants — it does not know what "Uniswap" is.
- **Routes are data, not code.** A compact byte-encoded route (`docs/specs/09-route-codec.md`) is
  decoded in Yul and executed hop-by-hop. Any language can build a route; new venues are new adapters,
  not new executors. This is what makes it plug-and-play.
- **Config over hardcoding.** Addresses, fees, and capabilities live in `config/chains/*.json`,
  verified and sourced. Nothing in `src/` hardcodes a chain address.
- **Prove every invariant.** Security-critical properties get invariant and (where feasible) symbolic
  tests, not just examples. `scripts/verify.sh` is the definition of done.
- **Lean beats clever-and-unproven.** Yul only where it measurably wins, always with a differential
  test against a readable reference.

## Architecture at a glance

```
        off-chain (any language)                         on-chain (per L2)
   ┌───────────────────────────────┐              ┌──────────────────────────────────┐
   │ scanner → route builder →      │  route bytes │  ArbExecutor (thin, hardened)    │
   │ dynamic sizer → simulator →    │─────────────►│   ├─ access control + pausable   │
   │ submitter (private/backrun)    │   execute()  │   ├─ reentrancy guard (transient)│
   └───────────────────────────────┘              │   ├─ flash borrow (IFlashProvider)│
            ▲                                       │   ├─ route dispatch (IDexAdapter)│
            │ quotes / sim / events                 │   ├─ repay loan                  │
            └───────────────────────────────────────┤   └─ profit invariant + sweep   │
                                                     └──────────────────────────────────┘
```
Full detail: `docs/ARCHITECTURE.md`.

## Phases

Each phase lists its **goal**, key **deliverables**, and an **exit criterion**. Do not advance a phase
until its exit criterion holds (later phases depend on it). Tasks are in `ralph/BACKLOG.md`.

### Phase 0 — Baseline & tooling
- **Goal:** a green, reproducible baseline every later task builds on.
- **Deliverables:** compiling skeleton; `scripts/verify.sh` gate; CI (default + `shanghai` profiles);
  gas snapshot; config-schema validation; an address-verification worklist.
- **Exit:** `scripts/verify.sh` GREEN in CI on both EVM profiles.

### Phase 1 — Chain & protocol registry
- **Goal:** turn every address/capability from "assumed" into "verified & sourced."
- **Deliverables:** verified chain params, predeploys/precompiles, flash-provider availability matrix,
  DEX registry, token lists — all in `config/chains/*.json` with `_source` citations.
- **Exit:** zero `"_verify": true` entries among the addresses any shipped code path uses.

### Phase 2 — Core executor & route codec
- **Goal:** the hardened dispatcher and the route format, provable against mocks.
- **Deliverables:** `RouteCodec` (Yul) with differential fuzz; `ArbExecutor` access control, pausable,
  reentrancy guard; `execute()` dispatch with profit invariant; validated flash-callback router;
  adapter interfaces + mock kit; invariant suite.
- **Exit:** a mocked 2-hop route executes atomically; malicious-callback and unprofitable-route tests
  revert; invariant suite passes.

### Phase 3 — Flash-loan provider adapters
- **Goal:** borrow from whichever provider is cheapest and available on a chain.
- **Deliverables:** Aave V3, Balancer V2 Vault, Uniswap V3 `pool.flash`, Uniswap V4 flash-accounting,
  DEX-native flash-swap adapters; a provider-selection library.
- **Exit:** each available provider round-trips a borrow→repay on a fork; selection picks the cheapest.

### Phase 4 — DEX adapters
- **Goal:** swap across every relevant venue family with quote parity.
- **Deliverables:** UniV2/Solidly CPMM, UniV3, UniV4, Solidly-CL, Algebra (Camelot), Curve, Balancer,
  TraderJoe LB adapters.
- **Exit:** each adapter's on-chain result matches its off-chain quote within tolerance on a fork.

### Phase 5 — Strategies
- **Goal:** compose adapters into the arbitrage shapes.
- **Deliverables:** 2-hop, triangular, and mixed-family cross-DEX route builders; negative-path tests.
- **Exit:** real-pool fork tests execute each shape profitably; unprofitable variants revert.

### Phase 6 — Dynamic loan sizing
- **Goal:** borrow exactly as much as the pools can absorb profitably.
- **Deliverables:** on-chain impact/minOut/minProfit guards; off-chain CPMM closed-form solver; CL
  depth model; ternary-search fallback; gas-adjusted breakeven; `size = min(...)` integration.
- **Exit:** fuzz proves size grows with depth, shrinks with thinness, and never breaches the impact
  bound; sized trades beat fixed-size on net profit in simulation.

### Phase 7 — MEV, ordering & gas
- **Goal:** win inclusion cheaply and keep the hot path tiny.
- **Deliverables:** per-chain tip module; private/sequencer-direct + backrun submission; Arbitrum
  Timeboost; Yul optimization pass; freshness/deadline guards.
- **Exit:** hot-path gas benchmarked and recorded; a documented submission strategy per chain.

### Phase 8 — Off-chain engine & multi-language SDKs
- **Goal:** make it usable from any application or language.
- **Deliverables:** language-neutral route-encoder + conformance vectors; TS/Python/Rust/Go SDKs;
  scanner; simulation gate; REST/gRPC gateway.
- **Exit:** every SDK reproduces the conformance vectors; the gateway can encode→quote→simulate→submit.

### Phase 9 — Cross-chain 2-hop
- **Goal:** capture cross-chain spreads under realistic (non-atomic) settlement.
- **Deliverables:** design note; inventory settlement contracts; bridge/intent adapter interface;
  off-chain orchestrator with hedging and hard exposure caps.
- **Exit:** a cross-chain route settles in simulation with correct inventory accounting and a capped,
  hedged exposure model. (Human sets exposure caps.)

### Phase 10 — Hardening, deployment & release
- **Goal:** audit-ready and deployable with deterministic addresses.
- **Deliverables:** Echidna/Foundry invariant campaign; Slither/Semgrep clean; Halmos/Certora proofs;
  CREATE2 deterministic deploy scripts; operator runbook + monitoring; audit-prep packet.
- **Exit:** invariants proven, static analysis triaged, deterministic deploys ready. (Human commissions
  an external audit before any mainnet capital.)

## Human decision points (surfaced by the loop)

The loop will stop and ask (via `ralph/PROGRESS.md` → `### NEEDS HUMAN`) for:
- Capital & risk limits: max notional/trade, min profit (bps + absolute), per-chain gas ceiling.
- Provider/venue prioritization where several are viable on one chain.
- Cross-chain exposure caps and acceptable settlement latency.
- Enabling any **live deployment or broadcast** — always a manual, audited, human action.

## Definition of done (every task)
`bash scripts/verify.sh` GREEN · tests added (happy + adversarial; fuzz for math; invariant for
security) · spec read and, if deviated from, updated · backlog checked off · `PROGRESS.md` appended ·
one conventional commit referencing the task id.
