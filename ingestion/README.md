# L2 Data Ingestion Layer — Ralph-loop build guide

The **data-feed component** of a Cross-Chain Flash-Loan Arbitrage app. It streams
live, on-chain-verified pool state from five L2s and feeds the `l2arb` Python
detection engine synchronized snapshots in **single-digit milliseconds**, then
fans the ranked opportunities out to the GUI and execution components.

- **Chains:** Arbitrum One (42161), Base (8453), Optimism (10), Unichain (130),
  Ink (57073).
- **Role:** pure read path. It holds no keys, signs nothing, submits no
  transactions.
- **How it gets built:** by a **Ralph loop** — a stateless coding agent run in a
  loop against the spec and tests in this repo, building the component in small,
  green, committed steps until it declares itself done.

> This repository is the **build guide + loop harness** *and* the component the
> loop built. The Ralph loop has walked milestones **M0–M10**: a Rust workspace of
> 13 crates + the `l2-ingest` binary, Tier-A green (`fmt`, `clippy -D warnings`,
> `cargo test`), with every on-chain test proven against **recorded real data at
> pinned blocks**. Two gates remain **BLOCKED** on infrastructure this environment
> lacks — the live `l2arb` engine (not on PyPI) and a WebSocket endpoint (public
> RPCs are HTTP-only) — so the loop has **not** written `RALPH-COMPLETE`
> (`ralph/PROGRESS.md` records exactly what's done and what's blocked, never faked).

---

## The two headline decisions

**1. One process, many chains (not five separate bots).**
A single Rust process runs one supervised async **ingestor task per chain**,
feeding one shared **aggregator**. Each chain has its own WebSocket, decoder, and
in-memory mirror (isolated failure domains, true parallel I/O), but a single
aggregator assembles the synchronized cross-chain snapshot and owns the one
connection to the engine. This is faster (no cross-process hops), genuinely in
sync (atomic per-chain snapshots), plug-and-play (one artifact), and lower-bug
(one coherent state) than five separate bots. Full rationale:
[`docs/ARCHITECTURE.md §1`](docs/ARCHITECTURE.md).

**2. Rust + `alloy` + `tokio`.**
No GC pauses → predictable millisecond tails; the engine is language-agnostic
over JSON, so no Python bindings are needed. The key latency trick: read new pool
state **out of the event the node already pushed** (`Sync`/`Swap` logs carry
post-trade reserves / `sqrtPrice`), so the hot path has **no extra RPC
round-trip**. [`docs/ARCHITECTURE.md §2, §8`](docs/ARCHITECTURE.md).

---

## Repository map

| Path | What it is |
|------|-----------|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | The design: one-process decision, data flow, module layout, latency budget, reorg/verify, sync policy, **testing strategy**. |
| [`docs/BUILD_PLAN.md`](docs/BUILD_PLAN.md) | Phased milestones **M0–M10**, each with test-based exit criteria. The loop walks this. |
| [`docs/ENGINE_CONTRACT.md`](docs/ENGINE_CONTRACT.md) | How on-chain state maps onto the engine's JSON (incl. **Uniswap V4** handling, gas model, `native_price_in`, the `verified` honesty flag). |
| [`docs/reference/INTEGRATION.md`](docs/reference/INTEGRATION.md) | The engine team's contract, **verbatim** (immutable — conform to it). |
| [`ralph/PROMPT.md`](ralph/PROMPT.md) | The fixed prompt fed to the agent every iteration. |
| [`ralph/AGENTS.md`](ralph/AGENTS.md) | Invariant rules (real data only, never fake `verified`, never reward-hack tests, always green, one task/iteration). |
| [`ralph/PROGRESS.md`](ralph/PROGRESS.md) | The loop's on-disk memory: checklist, next task, blockers, log. |
| [`ralph/loop.sh`](ralph/loop.sh) | The runner: feeds `PROMPT.md` to a fresh agent context until `RALPH-COMPLETE`. |
| [`config/config.example.toml`](config/config.example.toml) | The entire plug-and-play surface: chains, endpoints, pools, gas, cross-chain, engine, output. |
| [`config/pools/`](config/pools/) | Per-chain pool registries, validated on-chain at startup. |

---

## Run the Ralph loop

Prereqs: the coding agent CLI (e.g. `claude`), a Rust toolchain, and the `l2arb`
engine installed for the contract tests (`pip install l2arb` and/or its HTTP
service running).

```bash
# from the repo root
./ralph/loop.sh                 # loop until the build declares RALPH-COMPLETE
MAX_ITER=20 ./ralph/loop.sh     # or cap the number of iterations
```

Each iteration, the agent (fresh context) reads the rules and progress, finds the
first milestone with unmet exit criteria, does **one** task, proves it with
tests, makes the Tier-A suite green, updates `ralph/PROGRESS.md`, and commits.
The loop stops when every milestone's exit criteria pass and the agent writes the
`RALPH-COMPLETE` sentinel. Logs land in `ralph/logs/`.

**Why a loop?** A stateless agent + all state on disk (spec, plan, progress,
tests) turns a big, bug-prone build into many small, verifiable, green steps —
exactly the "least bugs and errors" outcome this component needs.

---

## Build & run (plug-and-play)

```bash
cargo build --release                              # builds the l2-ingest binary
./target/release/l2-ingest --check-config \        # validate a config, print a summary
  --config config/config.example.toml

cp config/config.example.toml config.toml          # then fill in your RPC endpoints
uvicorn l2arb.api.http:app --port 8080             # start the engine (once, long-running)
./target/release/l2-ingest --config config.toml    # start the feed
```

> **RPC endpoints & rate limits:** each chain's `ws_url`/`http_url` may be a
> comma-separated `primary, backup` list. Off-loop reads (seed / gate / reconcile)
> are Multicall3-batched to minimise request volume, and HTTP reads fail over to a
> backup endpoint on a rate-limit (429) or transport error.

Or with Docker: `docker build -t l2-ingest . && docker run -v $PWD/config.toml:/etc/l2-ingest/config.toml -p 9001:9001 -p 9090:9090 -p 9100:9100 l2-ingest`.

Tier-A tests: `cargo test --workspace`. Hot-path benches: `cargo bench -p l2i-benches`.
Live smoke / soak: `L2I_LIVE=1 ./scripts/soak.sh`.

- **Input:** one `config.toml`.
- **Output:** a stable, versioned envelope
  (`{ schema_version, kind, chain_blocks, payload }`) over WebSocket (default),
  NDJSON stdout, Redis, or gRPC — for the GUI and execution engine.
- **Ops:** single binary/container, `GET /health`, Prometheus `/metrics`,
  structured logs, graceful shutdown, `SIGHUP` config reload.

---

## What "done" means (the honest bar)

- **Tier A tests 100% green on every commit** — deterministic proofs over *real*
  on-chain data frozen at pinned blocks: event-derived state **==** independent
  `eth_call` at that block, contract tests against the **real** `l2arb`, reorg and
  property tests.
- **Tier B green pre-release** — latency benches within budget, a clean live
  **soak** (continuous on-chain reconciliation stays 100%, no memory growth,
  reconnect exercised), and an end-to-end plug-and-play run against live
  endpoints.

No mock data in the shipped path, ever. The `verified` flag is never faked. Tests
are never weakened to go green. See [`docs/ARCHITECTURE.md §9`](docs/ARCHITECTURE.md)
and [`ralph/AGENTS.md`](ralph/AGENTS.md).

---

## Verified infrastructure facts (checked live, July 2026)

| Fact | Value |
|------|-------|
| Chain IDs | Arbitrum One `42161`, Base `8453`, Optimism `10`, Unichain `130`, Ink `57073` |
| Multicall3 (all 5 chains) | `0xcA11bde05977b3631167028862bE2a173976CA11` (OP Stack preinstall) |
| OP Stack `GasPriceOracle` / `L1Block` predeploys | `0x42…000F` / `0x42…0015` |
| Arbitrum `ArbGasInfo` precompile | `0x…006C` |
| Unichain liquidity venue | **Uniswap V4** (singleton + hooks) → dedicated V4 adapter (M6) |

---

*Component boundaries:* this repo builds **only** the data-ingestion feed. The
GUI dashboard, on-chain execution engine, and flash-loan contracts are separate
components; this one feeds them through the output sink and conforms to the
`l2arb` engine's contract.
