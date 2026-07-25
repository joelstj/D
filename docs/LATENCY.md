# Latency health monitoring

The bot is only useful if it surfaces an opportunity **before the edge decays**,
so the merged product measures how long data takes to cross the whole pipeline —
from the moment the ingestion layer reads on-chain state to the moment the
opportunity is painted in the dashboard — and attributes that time to each
component's internal stages. A bottleneck anywhere (event decode, detection
search, the WS hop, the dashboard scan) is then visible and actionable rather
than hidden inside one opaque "it feels slow" number.

There are **two independent health checks**:

1. **Pipeline latency** — the opportunity data path (this is the headline).
2. **Execution-readiness latency** — a *separate*, read-only probe of the
   on-chain execution path, deliberately decoupled from opportunity display.

---

## 1. Pipeline latency

### Measurement points (2–5 per component)

```
Rust ingestion ─────────────▶ Python engine ─────▶ Node backend ───────▶ React UI
 decode  (Prom)               build                parse                 (ws receive)
 build (hotpath)   ──POST──▶  detect    ──resp──▶  map                   render
 engine_roundtrip             rank                 scan          ──ws──▶  client E2E
 publish  (Prom)              serialize            fanout
 tick_e2e (Prom)                                   end_to_end
        │                        │                    │                     │
        └── origin_wall_ms stamped here … measured against Date.now() here ─┘
                         (single shared host clock)
```

| Component | Stages measured | Where |
|-----------|-----------------|-------|
| **ingestion** (Rust) | `decode` · `build` (hotpath) · `engine_roundtrip` · `publish` · `tick_e2e` | Prometheus `:9100/metrics` (all 5) + the envelope trace carries `build` + `engine_roundtrip` |
| **engine** (Python) | `build` · `detect` · `rank` · `serialize` | `timing` block on the `/detect` response |
| **dashboard** (Node) | `parse` · `map` · `scan` · `fanout` · `end_to_end` | `LatencyMonitor`, `GET /api/latency` + `/ws` |
| **frontend** (React) | `ingest → your browser` (origin → render) | computed client-side from the opportunity's anchor |

### How the trip is timed (single-host)

Every stage times **itself** with a monotonic clock (`perf_counter_ns` /
`Instant` / `performance.now`) — those durations are always exact. The *end to
end* number needs a common reference across four processes, so the ingestion
layer stamps **one wall-clock `origin_wall_ms`** at the start of each aggregator
tick. Because the launcher (and the `.exe`) runs the whole stack on one host, the
system wall clock is shared, and the dashboard measures `now_ms − origin_wall_ms`.
This is labelled *single-host wall clock* everywhere it appears; run the browser
on a different machine and the client hop absorbs the clock skew (the
backend-side `end_to_end`, both ends on the launcher host, stays exact).

`sum(stages) ≠ end_to_end` — stages overlap and buffers add dwell time — so the
UI reports the two separately and never equates them.

### The wire contract (additive, backward-compatible)

- **engine** → `/detect` response gains an optional
  `timing: { component, stages:[{stage, ms}], total_ms }`.
- **ingestion** relays that verbatim (the Rust `DetectResponse` carries an opaque
  `timing` passthrough) and adds an optional envelope field
  `latency: { origin_wall_ms, component:"ingestion", stages, total_ms }`.
  `schema_version` is **unchanged** — the field is omitted when absent, so older
  consumers are unaffected.
- **dashboard** reads both, times its own stages, and stamps each opportunity
  with `originWallMs` so the browser can close the trace.

### Where to see it

- **GUI:** the *Pipeline Latency* panel (right column) — per-component stage bars
  (p50 with a p95 label), the ingest→dashboard headline, and the
  ingest→your-browser number.
- **REST:** `GET /api/latency` — the rolling snapshot (last/avg/p50/p95/p99 per
  stage) also pushed over `/ws` as `{ type:"latency" }`.
- **Prometheus:** ingestion `:9100/metrics` — `l2i_decode_seconds`,
  `l2i_hotpath_seconds`, `l2i_engine_detect_seconds`, `l2i_publish_seconds`,
  `l2i_tick_e2e_seconds`.

---

## 2. Execution-readiness latency (separate, read-only)

A distinct health check for the **execution** path, deliberately kept out of the
opportunity pipeline. It measures how responsive read-only chain access is —
the ground an eventual human-authorised signer builds on — without ever moving
toward a trade.

- Stages: `rpc_block` (`eth_blockNumber`), `rpc_gas` (`eth_gasPrice`), and — only
  when an operator sets `FLASH_LOAN_EXECUTOR_ADDRESS` — `contract_view`, a
  `staticCall` (`eth_call`) of the executor's cheap `aavePremiumBps()` view.
- **Safety (root `CLAUDE.md` invariant 3):** strictly read-only. It never builds
  a signer, never sends a transaction, never calls `executeArbitrage` as a write,
  and never touches `LiveExecutor` (which still refuses to broadcast). Execution
  stays paper-by-default and human-gated. With no RPC configured (the paper
  default) it reports `configured:false` cleanly.
- Where: `GET /api/health/execution`, and its own section in the *Pipeline
  Latency* panel (polled every 5 s).

---

## Honesty

Latency is **real measured elapsed time** — observability, not market data — so
it is exempt from the "on-chain data only" rule but bound by the same honesty
discipline: nothing is fabricated, the end-to-end is labelled single-host wall
clock, and no stage sum is passed off as the end-to-end. Non-finite or negative
samples (a clock hiccup) are dropped rather than allowed to poison the
statistics.

## Testing

Every piece is unit-tested with injected clocks/fakes so the suite is
deterministic and offline: `engine/tests/obs/test_latency.py`,
`ingestion/crates/{observability,output,core}` tests,
`dashboard/backend/test/{latency,executionLatency,external,api}.test.ts`, and
`dashboard/frontend/src/**/{liveReducer,LatencyPanel}.test.*`.
