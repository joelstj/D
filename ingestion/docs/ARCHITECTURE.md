# Architecture — L2 Data Ingestion Layer

> The **data-feed component** of the Cross-Chain Flash-Loan Arbitrage app. It
> gathers live, on-chain-verified pool state from five L2s, keeps it fresh in
> memory, and feeds the `l2arb` Python detection engine synchronized
> `DetectRequest` snapshots in **single-digit milliseconds**, then fans the
> ranked opportunities out to the GUI and execution components.
>
> It holds no keys, signs nothing, submits no transactions. It is a **read path**.

Chains in scope: **Arbitrum One** (42161), **Base** (8453), **Optimism** (10),
**Unichain** (130), **Ink** (57073).

---

## 1. The central architecture decision — one process, many chains

> **Question posed:** one bot per chain in parallel, or a single bot for all
> chains? **Decision: a single Rust process running one supervised async
> _ingestor task per chain_, feeding one shared aggregator.** This is the "best
> of both" and is the recommended, lower-bug design.

**Why not five separate OS processes / repos:**
- Cross-chain arbitrage needs a *coherent, simultaneous* view of all chains. Five
  processes means five clocks, five caches, and cross-process IPC on the hot path
  — added latency and a whole class of "which snapshot goes with which" bugs.
- 5× the deployment, config, health, and upgrade surface. More moving parts →
  more failure modes → more bugs. The opposite of what was asked for.

**Why not one thread polling all chains sequentially:**
- Head-of-line blocking: a slow Ink response would stall Base. Unacceptable for a
  latency-critical feed.

**The chosen shape — concurrency _inside_ one process:**
- Each chain gets its **own** WebSocket subscription, event decoder, and in-memory
  pool mirror, running as an independent `tokio` task (an *actor*). Chains are
  **isolated failure domains**: Ink reconnecting cannot touch Base's data path.
- A **supervisor** restarts any ingestor that dies, with backoff, without taking
  down the others.
- One **aggregator** owns cross-chain snapshot assembly and the single connection
  pool to the engine. One binary, one config, one health endpoint, one metrics
  endpoint. Parallel where it helps (per-chain I/O), unified where it matters
  (the snapshot handed to the engine).

This directly serves every stated goal: **fast** (no cross-process hops, no
polling), **in sync** (one aggregator, atomic per-chain snapshots), **plug-and-play**
(one artifact), **fewest bugs** (isolated tasks, one coherent state).

---

## 2. Language & core stack — Rust

Chosen for the reasons the task emphasizes — *lightning fast, millisecond,
zero-GC-pause, ship-ready*:

| Concern | Choice | Why |
|--------|--------|-----|
| Language | **Rust (stable)** | No GC pauses (predictable p99), fearless concurrency, C-like throughput, strong types catch whole bug classes at compile time. The engine is language-agnostic over JSON, so **no Python bindings needed.** |
| Async runtime | **`tokio`** | Best-in-class async I/O, task supervision, timers. |
| Ethereum / RPC | **`alloy`** | The current standard Rust Ethereum stack (successor to the now-deprecated `ethers-rs`); first-class `U256`, ABI/event decoding, WS + IPC + HTTP providers, `sol!` typed bindings, Multicall. |
| Big ints | **`alloy_primitives::U256`/`I256`** | Exact 256-bit; serialize as decimal strings for the contract. |
| JSON | **`serde` + `serde_json`** | Contract (de)serialization with golden tests. |
| HTTP client → engine | **`reqwest`** (keep-alive pool) | Persistent low-latency calls to `l2arb`. |
| Config | **`figment`/`toml` + `serde`** | Typed, layered config with validation. |
| Metrics | **`metrics` + Prometheus exporter** | Latency histograms, health. |
| Tracing | **`tracing`** | Structured, low-overhead spans on the hot path. |
| Tests | `cargo test`, **`proptest`**, **`criterion`**, `anvil`/`reth` fork | See §9. |

> Go is a reasonable alternative (good concurrency, `geth` client) and the design
> ports to it 1:1. Rust wins on tail-latency determinism and `alloy`'s ergonomics.
> **If the build hits a hard Rust blocker, that is a decision to escalate in
> `PROGRESS.md`, not a reason to silently switch languages mid-loop.**

---

## 3. Data flow (the happy path, per update)

```
                    ┌─────────────────────────── one Rust process ───────────────────────────┐
 chain node (WS)    │                                                                          │
   newHeads,        │   ┌── Ingestor[Arbitrum] ──┐                                             │
   logs  ───push───▶│   │ WS sub → decode event  │──┐                                          │
                    │   │ update in-mem mirror    │  │                                          │
                    │   │ reconcile (eth_call)    │  │  per-chain                               │
                    │   └────────────────────────┘  │  verified                                │
                    │   ┌── Ingestor[Base] ───────┐  │  pool deltas    ┌───────────────────┐    │
   ... × 5 chains ─▶│   │  … same shape …         │──┼───────────────▶ │    Aggregator     │    │
                    │   └─────────────────────────┘  │                 │ • per-chain snap   │    │
                    │   ┌── Ingestor[Unichain V4] ┐  │                 │   (atomic, stamped)│    │
                    │   │  PoolManager logs by id │──┘                 │ • gas/price ctx    │    │
                    │   └─────────────────────────┘                    │ • native_price_in  │    │
                    │                                                   │ • cross_chain wire │    │
                    │                                                   │ • build DetectReq  │    │
                    │                                                   └─────────┬─────────┘    │
                    │                                        keep-alive POST /detect │           │
                    │                                                   ┌───────────▼─────────┐  │
                    │                                                   │  EngineClient →      │  │
                    │                                                   │  l2arb (HTTP/subproc)│  │
                    │                                                   └───────────┬─────────┘  │
                    │                                     {opportunities}           │            │
                    │                                                   ┌───────────▼─────────┐  │
                    │                                                   │   Output sink        │  │
                    │                                                   │  WS / gRPC / Redis   │──┼──▶ GUI + executor
                    │                                                   └──────────────────────┘  │
                    │   Supervisor restarts any dead ingestor · Observability scrapes every stage │
                    └──────────────────────────────────────────────────────────────────────────┘
```

**Key latency idea:** state is read *out of the event the node already pushed*
(the `Sync`/`Swap` log carries post-trade reserves / sqrtPrice), so the hot path
has **no extra RPC round-trip**. RPC `eth_call`/multicall is used only for
startup seeding and background reconciliation, off the hot path.

**RPC batching (rate-limit hygiene).** Every off-loop read path is batched so a
node's rate limit is never the bottleneck:
- **Seeding** — one Multicall3 `aggregate3` per pool-kind per chain (`getReserves`
  / `slot0`+`liquidity`).
- **Validation gate** — all of a registry's reads (`token0`/`token1`/`fee`/
  `factory`/`decimals`/`symbol`, deduped across pools that share a token) are
  pre-fetched in a handful of batched round-trips — one `eth_getCode` JSON-RPC
  batch plus chunked `aggregate3` — via `rpc::PrefetchProvider`, then the per-pool
  gate runs offline against them. Boot cost drops from O(pools) requests to O(1).
- **Reconcile** — a round's pools are grouped by their stamped block and each
  group's reads go out as a single `aggregate3` (one request per distinct block).

**Endpoint failover.** `http_url`/`ws_url` accept a comma-separated primary +
backup list. HTTP reads run through `rpc::failover`: on a rate-limit (429 /
JSON-RPC `-32005`) or transport error they hand off to the next endpoint and stick
to whichever answers — never failing over on a genuine revert (every endpoint
would return it identically).

---

## 4. Module / crate layout (Rust workspace)

```
l2-ingest/                     workspace root
├─ Cargo.toml                  [workspace]
├─ crates/
│  ├─ core/          domain types; engine JSON contract (DetectRequest/Response,
│  │                 Pool, Token, Blockstamp); U256↔decimal-string; golden tests
│  ├─ amm/           pure math: V2 getAmountOut, V3/V4 tick math, spot price,
│  │                 native_price_in derivation — no I/O, exhaustively unit-tested
│  ├─ rpc/           alloy providers: WS subscribe, HTTP archive, Multicall3,
│  │                 reconnect w/ backoff, request coalescing, provider trait
│  ├─ chains/        per-chain params & predeploys; gas adapters
│  │                 (OpStackGasOracle @0x…0F, ArbGasInfo @0x…6C)
│  ├─ ingest/        per-chain Ingestor actor: event decode, in-mem mirror,
│  │                 blockstamping, reorg handling, reconciliation, verified flag
│  ├─ v4/            Uniswap V4 adapter: PoolManager logs by poolId, StateView
│  │                 reconcile, hook safety gate, dynamic-fee read → v3 shape
│  ├─ aggregator/    atomic per-chain snapshots, sync policy, request builder,
│  │                 cross_chain wiring, cadence/debounce, incremental mode
│  ├─ engine-client/ EngineClient trait; HTTP keep-alive impl + subprocess impl;
│  │                 timeout, retry, backpressure, response validation
│  ├─ output/        outbound sink trait: WS server / stdout / Redis / gRPC
│  ├─ observability/ metrics, latency histograms, health server, tracing setup
│  └─ app/           the binary: load config → validate → supervisor → wire all
├─ config/
│  ├─ config.example.toml
│  └─ pools/         per-chain verified pool registries (checked in, boot-validated)
├─ tests/            integration, contract-with-engine, reorg, e2e
├─ benches/          criterion hot-path latency/throughput
└─ scripts/          live soak + on-chain verification harness (nightly/pre-release)
```

Each crate is independently unit-testable; dependencies flow downward
(`core` ← `amm` ← `ingest`/`v4` ← `aggregator` ← `app`). This ordering is exactly
the build-plan milestone order (see `BUILD_PLAN.md`), so every milestone leaves a
green, self-contained layer.

---

## 5. The per-chain Ingestor (the heart)

Lifecycle of one ingestor actor:

1. **Boot & validate** — load this chain's pool registry; for *every* pool and
   token, run the on-chain validation gate (§7). Reject/park invalid entries with
   a loud log. Seed the mirror with a `getReserves`/`slot0`+`liquidity` multicall
   at the current head. Only validated pools enter the live set.
2. **Subscribe** — open a WebSocket, `eth_subscribe(newHeads)` and
   `eth_subscribe(logs)` filtered to the registry's pool addresses (V4:
   `PoolManager` filtered by poolId topics). Fall back to HTTP poll only if WS is
   unavailable (degraded mode, logged).
3. **Ingest** — on each relevant log: decode → update mirror → mark the pool
   dirty with the new `blockstamp`. On each `newHead`: finalize the block's dirty
   set, confirm the head's parent matches our last hash (reorg check, §6), and
   push the verified delta to the aggregator.
4. **Reconcile** (background, off hot path) — every `reconcile_interval_ms`, take
   a rotating subset of pools and independently read their state at each pool's
   pinned block; assert equality with the mirror. The subset's reads are grouped by
   block and batched into one `aggregate3` per block (not one `eth_call` per pool).
   Mismatch → mark `verified:false`, re-seed, alarm. This is the continuous proof
   that our data is *real*.
5. **Supervise** — any panic/disconnect → the supervisor restarts the actor with
   exponential backoff; during re-seed the chain's pools emit `verified:false`.

State is **all on the heap in the mirror** (a `DashMap`/sharded map of
`pool → PoolState`), so reads by the aggregator are lock-cheap and allocation-free
on the hot path.

---

## 6. Correctness: reorgs, verification, and "never lie"

- **Reorg handling.** Track a short ring buffer of recent `(number, hash,
  parent_hash)` per chain. If a new head's `parent_hash` ≠ our stored hash for
  `number−1`, a reorg occurred: walk back to the common ancestor, mark all pools
  touched in the rolled-back blocks `verified:false`, re-derive from canonical
  logs, then restore `verified:true`. L2 reorgs are rare but real (sequencer
  hiccups); we handle them rather than assume they can't happen.
- **`verified` semantics** are defined in `ENGINE_CONTRACT.md §6` and are the
  system's honesty contract: `true` only when reproducible from the canonical
  chain at the stamped `block_hash` **and** the latest reconciliation matched.
- **Never fabricate.** No mock reserves, no hardcoded prices, no
  test-only `verified:true`. If data can't be verified, it ships as
  `verified:false` and is logged. Tests assert this property; the loop's rules
  (`ralph/AGENTS.md`) forbid weakening it.

---

## 7. On-chain validation gate (every address, current & correct)

At startup (and on registry hot-reload) each pool entry passes, or it does not
enter the live set:

1. `eth_getCode(pool) != 0x` — the contract exists.
2. Read `token0()`,`token1()` (V3) / `getReserves` ordering (V2) / `PoolKey` (V4)
   → must equal the registry's declared tokens.
3. Read `fee()` (V3) / `PoolKey.fee` (V4) → must equal declared `fee_pips`.
4. Read each token's `decimals()` and `symbol()` → cache; validate `0…36`.
5. Confirm the pool's `factory()` (where available) is an expected/known factory
   for that DEX — guards against look-alike/malicious pools.
6. V4 only: `hooks` ∈ {`0x0`, `safe_hooks[chain]`}, else reject (§4).

This is what makes "every route, address, connection is current and correct" a
*tested guarantee* rather than a hope. The same gate runs in CI against a pinned
block (deterministic) and in the live soak (current).

---

## 8. Latency budget & how we hit it

Honest framing: **true zero latency is bounded by how fast the node hands us the
block.** We drive everything *we* control to the floor and recommend node
locality for the rest.

| Stage | Target (p99) | How |
|-------|-------------:|-----|
| Node → bot (WS push) | network-bound (~1–50 ms) | Persistent WS; **co-located / dedicated low-latency RPC** (or self-hosted node). This dominates — optimize endpoint locality. |
| Decode event | < 100 µs | `alloy` typed decode, no allocation on hot path |
| Update mirror | < 50 µs | sharded in-memory map, no lock contention |
| Assemble snapshot + build `DetectRequest` | < 1 ms | pre-sized buffers, `incremental:true` sends only deltas |
| Engine call (localhost, keep-alive) | ~0.2–2 ms + engine compute | persistent `reqwest` pool; engine is separate process |
| Output emit | < 1 ms | non-blocking broadcast channel |
| **Intra-process hot path (decode→emit, excl. node link & engine compute)** | **< 5 ms** | measured by `criterion` + a live harness; a CI-enforced budget |

Latency enablers, in priority order:
1. **Event-carried state** — no per-block RPC round-trip (the big win).
2. **Persistent connections everywhere** — WS to nodes, keep-alive HTTP to engine.
3. **Incremental requests** — after the first snapshot, send only changed pools.
4. **In-memory mirror** — the current state of every pool is always resident.
5. **(Advanced, chain-specific) Flashblocks pre-confirmations** — Base and
   Unichain expose ~200 ms sub-block pre-confirmations (Flashbots-built
   sequencer). Subscribing to these can surface state ~1–2 s earlier than the full
   block. **Trade-off:** pre-confirmations are less final, so state derived from
   them ships `verified:false` until the full block confirms. Gated behind a
   config flag, delivered in hardening (M10), never on by default.

---

## 9. Testing strategy — "100% passing" with *real* data, honestly

The tension: "live real-world data" is nondeterministic; "100% passing CI" needs
determinism. We resolve it with a **two-tier** strategy — both tiers are required
for "ship ready," but only the deterministic tier gates the Ralph loop, so the
loop never chases flaky failures.

### Tier A — deterministic (gates every commit; must be 100% green)
Real on-chain data, *frozen* at pinned blocks (reproducible), plus pure logic:

1. **Unit** — event decoding vs recorded real mainnet log fixtures; AMM math vs
   known-answer vectors; big-int decimal-string round-trips at 2¹⁶⁰ scale; config
   validation; contract (de)serialization vs golden files derived from
   `reference/INTEGRATION.md`.
2. **Property (`proptest`)** — invariants: reserves never negative; blockstamp
   monotonic per chain; a snapshot never mixes two blocks of one chain;
   `0≤decimals≤36`; `verified:false` whenever a reconcile mismatch is injected.
3. **On-chain verification (pinned block, forked via `anvil`/`reth`)** — for real
   pools on each chain at block N: assert **event-derived mirror == independent
   `eth_call` state at N**, exactly. This is the reproducible proof the data is
   real. Runs in CI against a cached fork.
4. **Contract-with-engine** — `pip install` the real `l2arb`, run it (subprocess
   or a spun-up HTTP service), feed it a `DetectRequest` assembled from pinned
   real state, assert a schema-valid response, `net_profit>0`, and blockstamp
   round-trip. Proves the two components integrate flawlessly.
5. **Reorg / chaos (simulated)** — inject conflicting block hashes, dropped WS,
   slow/erroring RPC → assert rollback, `verified:false`, supervised recovery, no
   stale emission, no crash.

### Tier B — live (required for release; runs nightly + pre-release, not per-commit)
6. **Latency/throughput benches (`criterion`)** — hot-path p50/p99 vs the §8
   budget; fails the release if over budget.
7. **Live soak** — all five chains, real endpoints, for a sustained window:
   continuous reconciliation must stay **100%**, zero memory growth, WS
   auto-reconnect exercised, sampled emitted snapshots re-verified on-chain.
8. **End-to-end plug-and-play** — boot the whole component from
   `config.example.toml` against live endpoints and a live `l2arb`; assert
   opportunities flow to the output sink within budget. The "does it actually
   work, end to end, for a new integrator" gate.

**Definition of "done" for the whole component:** Tier A 100% green on every
commit, **and** Tier B green on the nightly/pre-release run (benches within
budget, soak clean, e2e passes). Only then does the loop write `RALPH-COMPLETE`.

> A test that can't run (e.g. a live endpoint is down) is marked **blocked with a
> reason** in `PROGRESS.md` — never deleted, skipped-and-forgotten, or faked
> green. Reward-hacking the test suite is the one unforgivable move here.

---

## 10. Plug-and-play integration surface

For downstream components (GUI dashboard, execution engine, flash-loan contracts)
the component is deliberately boring to adopt:

- **Input:** one `config.toml` (chains, endpoints, pool registries, gas params,
  cross-chain, engine URL, output sink). Copy the example, fill endpoints, run.
- **Output:** a stable, versioned envelope pushed to the configured sink —
  `{ schema_version, kind: "snapshot"|"opportunities", chain_blocks:{…}, payload }`
  — over WebSocket (default), stdout (NDJSON), Redis stream, or gRPC. Consumers
  subscribe; no coupling to our internals.
- **Operational:** single static binary (or container), `GET /health`,
  Prometheus `/metrics`, structured logs, graceful shutdown, `SIGHUP` config
  reload. 12-factor. One thing to deploy.
- **The engine stays decoupled** behind the `EngineClient` trait, so HTTP vs
  subprocess (or a future gRPC) is a config choice, and the same component works
  whether `l2arb` runs locally, in a sidecar, or over the network.

---

## 11. Non-goals (explicit, so the loop stays in its lane)

- No transaction signing, submission, or key custody (that's the execution engine).
- No flash-loan contracts (separate component).
- No GUI (separate component; we *feed* it via the output sink).
- No new AMM math in the engine — if a pool type isn't V2/V3-representable, it's
  excluded, not approximated (`ENGINE_CONTRACT.md §1`).
- No changes to the engine's JSON contract — we conform to it.
