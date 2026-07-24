# Spec 11 — Off-chain engine & multi-language SDKs

The contracts are the safe execution core; the off-chain engine is the brain that finds opportunities,
sizes them, proves they pay, and lands them. "Integrate with any application or language" is a
first-class requirement, so the design is: **one small language-neutral contract (route bytes) + thin
SDKs + an HTTP/gRPC gateway.**

## Pipeline (reference implementation lives in `offchain/`)

```
scanner → route builder → dynamic sizer → encoder → simulator (GATE) → submitter → monitor
```

- **Scanner** — subscribe to pool state / swaps per chain; maintain a live price/reserve view across the
  configured DEXes; emit candidate cycles (2-hop, triangular, cross-DEX). Cheap pre-filter first
  (`M > N` CPMM test, `docs/specs/05`) before heavier quoting.
- **Route builder** — assemble candidate hops, choose venues *at the sized amount*, both directions
  (`docs/specs/04`).
- **Dynamic sizer** — `size = min(profit-optimal, depth-bound, flash-liquidity, risk-cap)` with
  gas-adjusted breakeven (`docs/specs/05`).
- **Encoder** — produce route bytes (`docs/specs/09`).
- **Simulator (mandatory gate)** — `eth_call` against the executor and/or a local fork; **discard any
  route that isn't net-profitable after gas + flash fee.** Nothing skips this gate (**P8-T7**).
- **Submitter** — private/sequencer-direct, backrun bundle, or Timeboost, with the profit-capped tip
  (`docs/specs/06`). Holds no keys by default; can return an unsigned tx for an external signer.
- **Monitor** — realized profit, revert-rate (high is healthy — the guard doing its job), gas, and
  cross-chain inventory drift; Prometheus metrics + alert thresholds.

## SDK surface (identical across languages)
Every SDK exposes the same nouns/verbs so switching languages is mechanical:
```
connect(chain, rpc, signer?)            -> client
client.route()                          -> builder: .flashFrom / .swap / .sizeToDepth / .build
client.quote(route)                     -> expected output/profit (view)
client.simulate(route, {minProfitBps})  -> { profitable, profit, gas, minProfit }
client.submit(route, {minProfit, mode}) -> txHash | unsignedTx
client.chains / client.config           -> registry access (addresses, capabilities)
```
- **TypeScript** (`sdk/typescript`, P8-T2) — first-class, viem/ethers based.
- **Python** (`sdk/python`, P8-T3), **Rust** (`sdk/rust`, P8-T4), **Go** (`sdk/go`, P8-T5) — parity.

## The thing that keeps every language honest: conformance vectors
The encoder is the only piece that *must* be byte-identical everywhere. `docs/specs/vectors/*.json`
(P8-T1) are `{ description, fields, expectedHex }` cases; each SDK's test suite asserts it reproduces
every `expectedHex`. A language port isn't "done" until it passes all vectors. This is why we can
credibly claim "any language" without maintaining four diverging implementations by hand.

## Gateway for non-SDK languages (P8-T8)
A REST + gRPC sidecar wrapping build/quote/simulate/submit (see `docs/INTEGRATION.md` for endpoints) so
a service in *any* language integrates over HTTP with zero crypto libraries. Keyless by default:
`submit` returns an unsigned tx unless a signer is explicitly configured. This is the widest-reach
integration path.

## Config, not constants
Both on- and off-chain read `config/chains/*.json` (`docs/specs/01`). The SDK config loader validates
against `config/chains/schema.json` and refuses to operate on entries still flagged `"_verify": true`
for any address it would actually use — a guardrail against shipping an unverified address into a live
path.

## Simulation & backtesting
- **Live sim gate** — `eth_call`/fork dry-run immediately before submit (mandatory).
- **Backtest harness** — replay historical blocks/pool states to evaluate strategy/sizing changes
  offline before they touch a live path (added alongside Phase 8; deterministic, no live calls).

## Observability & SLOs
Metrics: opportunities seen, simulated-profitable, submitted, landed, reverted, realized profit, gas
paid, tip paid, per-chain latency, (cross-chain) inventory drift. Alert on: revert-rate spike beyond
baseline, negative realized PnL over a window, inventory drift past threshold, submitter/RPC errors.
Runbook: `docs/INTEGRATION.md` (Phase 10, P10-T5).

## Language/runtime notes
- Node ≥ 20 for the TS SDK + gateway; Python ≥ 3.11; recent stable Rust/Go.
- Nothing in the off-chain layer holds secrets in code — keys come from env/KMS/hardware signer.
- The reference `offchain/` is a working example, not the only way to consume the engine — the contract
  seam (route bytes) means teams can build their own scanner and still use the executor unchanged.
