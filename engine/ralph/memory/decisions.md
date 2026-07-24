# Architecture Decision Records (ADRs)

Lightweight, append-only. One record per real decision so a future iteration can
reverse it deliberately, not accidentally. Format:

```
## ADR-NNN — <title>
Date: <ISO>  ·  Status: accepted | superseded by ADR-MMM
Context: <the forces at play>
Decision: <what we chose>
Consequences: <trade-offs, what this makes easy/hard>
```

---

## ADR-001 — Detection-only, never execution
Date: 2026-07-13 · Status: accepted
Context: On-chain arbitrage can be extended to execution/MEV, which carries key-
custody, financial, and ethical risk. The brief asks for a detection engine.
Decision: The system reads only public on-chain data and emits opportunities. It
holds no keys, signs nothing, submits no transactions. Enforced by static tests.
Consequences: Smaller attack surface; simpler safety story. Any execution ask is
out-of-scope and must go to `blocked.md` for a human decision.

## ADR-002 — Ports & adapters; pure core
Date: 2026-07-13 · Status: accepted
Context: We must add new L2s and DEX families without rewrites, and unit-test math
without a network.
Decision: `amm/`, `graph/`, `detect/`, `model/` are pure and import no I/O.
Chains/DEXes/stores/oracles plug in behind typed `Protocol` ports. An import-
linter contract enforces the boundary in CI.
Consequences: New chain/DEX = an adapter + tests. Slight indirection cost.

## ADR-003 — On-chain data only in runtime; two-source verification
Date: 2026-07-13 · Status: accepted
Context: The brief requires "only real live blockchain data verifiable on-chain."
Decision: Runtime values are read from RPC or derived from such reads; every value
carries a `Blockstamp`; pools are trusted only when a primary RPC and an
independent oracle (Blockscout MCP / 2nd RPC) agree at the same block. Off-chain
data (CCXT/CEX/aggregators) is confined to offline research.
Consequences: Strong integrity guarantee; extra verification work and latency
handled out-of-band.

## ADR-004 — uv + ruff + mypy(strict) as the toolchain
Date: 2026-07-13 · Status: accepted
Context: Need reproducible envs and a fast, strict quality gate.
Decision: `uv` for env/lock, `ruff` for format+lint (incl. security lints),
`mypy --strict` for types. One tool per job; locked deps committed.
Consequences: Fast, deterministic; poetry/black/flake8 not used.

## ADR-005 — Detection = graph candidate-gen + exact re-pricing
Date: 2026-07-13 · Status: accepted
Context: Negative-cycle search on `-ln(rate)` finds candidates cheaply but ignores
size/slippage.
Decision: Use SPFA/Bellman-Ford (incremental) + tropical K-hop (sweep) to generate
candidate cycles, then re-price each with exact AMM math at an optimized size
before applying the net-profit gate.
Consequences: Fast detection without false "free money"; two-stage pipeline.

## ADR-007 — V3 pricing: exact single-tick + conservative capping (no fabricated liquidity)
Date: 2026-07-13 · Status: accepted
Context: Exact Uniswap V3 output for a large swap requires crossing initialized
ticks, which needs the tick bitmap + per-tick `liquidityNet` read on-chain. In the
pure-math layer we do not always have that data, and *over*-stating output would
manufacture phantom arbitrage (a false positive) — the worst failure for a
detector.
Decision: Implement the single-tick swap step faithfully to Uniswap core
(`SqrtPriceMath`/`SwapMath`) with exact rounding. When a swap would move price past
a supplied `sqrt_price_limit_x96` (the next initialized tick), cap the step at that
boundary and return the partial output. Without tick data the output is therefore a
*lower bound* on the true output. Understating can only suppress an opportunity,
never invent one. The `chain`/`verify` tiers assert local == QuoterV2 within 1 wei
(T-0304) once Anvil is available.
Consequences: Safe by construction; some large-size V3 opportunities are
under-counted until the engine feeds real tick data. All exact math stays in Python
`int` (sqrtPrice reaches ~2**160) — never lowered to fixed-width `numba`/`numpy`.

## ADR-006 — Latency is a measured, gated SLO (Python-first, Rust escape hatch)
Date: 2026-07-13 · Status: accepted
Context: "Near-zero-latency" must be concrete and defended against regressions.
Decision: Per-stage budgets + p99 SLO enforced by `pytest-benchmark` in CI. Stay
Python (numba/numpy) unless benchmarks prove it misses SLO, then allow a Rust
(pyo3) implementation of the cycle search behind the same port.
Consequences: Objective latency bar; optional native complexity only if justified.
