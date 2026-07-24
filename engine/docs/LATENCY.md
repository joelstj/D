# Latency — making "near-zero-latency" concrete

> "Near-zero-latency" is meaningless unless it is measured and gated. Here it is
> a **per-stage budget** with **p99 SLOs** asserted by benchmark tests in CI. A
> regression past tolerance fails the build.

## 1. What we optimize

The **event→emit** path: the wall-clock from receiving a chain event (new block /
`Sync` / `Swap` log over WSS) to emitting a verified-provenance opportunity.
Verification is out-of-band (§ARCHITECTURE 3), so it is *not* on the emit path;
correctness of emission still requires the provenance stamp.

## 2. Budget (initial targets — tune per chain in config)

Default SLO: **p99 event→emit ≤ `MAX_LATENCY_MS_P99` (250 ms)**, p50 ≤ 40 ms, on
a warm graph. Per-stage soft budgets (p99):

| Stage | Budget | Notes |
|---|---|---|
| WSS receive + `orjson` decode | ≤ 3 ms | no Python-level JSON |
| log → pool-state update (O(1)) | ≤ 1 ms | in-place reserve/tick edit |
| dirty-set propagation | ≤ 2 ms | only affected tokens |
| incremental cycle search | ≤ 15 ms | bounded hops, `numba` inner loop |
| exact re-price + optimal size | ≤ 5 ms | golden-section, few AMM evals |
| net-profit gate + build record | ≤ 2 ms | |
| emit to sink | ≤ 2 ms | async, non-blocking |

Cold-start (initial full graph load) is a separate budget dominated by RPC; use
**multicall** to bound it to a few round-trips, not thousands.

## 3. Techniques (in priority order)

1. **Don't recompute the world.** Event-driven O(1) state updates; incremental,
   dirty-seeded detection instead of full-graph sweeps every block.
2. **Never block the event loop.** All I/O is async; ruff `ASYNC` lints guard
   against sync calls sneaking onto the hot path. CPU math is offloaded to
   `numba` functions that release the GIL.
3. **Compiled hot loops.** AMM math and the min-plus/SPFA inner loops are
   `numba`-JIT (or `numpy`-vectorized). Warm up JIT at startup so first-event
   latency isn't paying compilation.
4. **Zero-copy, low-allocation.** Preallocate arrays; mutate reserves in place;
   avoid per-event object churn on the hot path (object pools where it matters).
5. **Batch the cold path.** `multicall` for initial state; batch `eth_getLogs`
   backfill; keep the streaming path lean.
6. **Coalesce under load.** Backpressure keeps only the latest state per pool; we
   want *current* truth, not every intermediate tick.
7. **Locality.** Prefer RPC providers/regions close to the sequencer; support a
   local node. (Ops concern, but it dominates real-world latency.)

## 4. Escape hatch

If Python cannot hold the search budget at the target graph size, implement
`graph/negcycle` in **Rust (pyo3)** behind the existing port (ADR required).
Measured, not assumed — only after benchmarks prove Python is the bottleneck.

## 5. How it's enforced (benchmark tier)

- `pytest-benchmark` measures each stage and the end-to-end path on representative
  warm state; baselines saved with `--benchmark-autosave`.
- CI asserts p99 ≤ budget and fails on a regression beyond tolerance.
- Prometheus exports the **live** latency histogram; an SLO breach in production
  raises an alert (Phase 10). Benchmarks guard regressions pre-merge; metrics
  guard reality post-deploy.

## 6. Honest caveats

- **Detection latency ≠ capture latency.** This engine *detects*; it does not
  race to execute. "Near-zero" here means the detector reflects new state almost
  immediately, not that an opportunity is captured before others.
- Python has real floors (GC, interpreter). The budgets above are achievable for
  detection with the techniques listed; sub-millisecond end-to-end at large graph
  sizes is where the Rust hatch exists. We measure before promising.
