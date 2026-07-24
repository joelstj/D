# l2arb — Layer-2 arbitrage **detection** engine

Off-chain, near-zero-latency detection of arbitrage opportunities on Layer-2
blockchains. It watches live on-chain DEX state and emits opportunities:

- **Single chain:** 2-hop (spatial), triangular, and bounded multi-hop cycles.
- **Cross chain:** simple 2-hop spreads (same asset, two chains).

It is a **detector and analytics system, not a trader** — it holds no keys, signs
nothing, and submits no transactions. Every number it reports is derived from
**live, on-chain-verifiable state** and stamped with the exact block it came
from.

> ⚠️ This repository is being built autonomously by a **Ralph loop** (a fresh
> Claude Code context each iteration, driven by a plan on disk). If you're here
> to understand or steer that build, start with **`ralph/OPERATING_MANUAL.md`**.

## Why it's built this way

| Requirement | How it's met | Where |
|---|---|---|
| Real, verifiable data only | RPC reads + two-source verification (Blockscout / 2nd RPC); block-stamped; no synthetic data in runtime | `docs/DATA_INTEGRITY.md` |
| Near-zero latency | event-driven O(1) state updates, incremental cycle search, `numba` hot loops, p99 SLO gated in CI | `docs/LATENCY.md` |
| Correct arbitrage math | AMM math + negative-cycle detection, property-tested and cross-checked vs on-chain quoter | `docs/ARBITRAGE_THEORY.md` |
| Elegant / evolvable | ports & adapters; pure core; add-a-chain/DEX = an adapter | `docs/ARCHITECTURE.md` |
| Tested to 100% green | TDD, tiered test taxonomy, coverage ratchet, every module paired with a test | `docs/TESTING_STRATEGY.md` |
| Detection-only, safe | no keys / no signing / no execution, enforced by static tests | `docs/SECURITY.md` |

## Documentation map

- **Plan:** `docs/BUILD_PLAN.md` · `plan/backlog.md` · `plan/milestones.md`
- **How it's built (the loop):** `CLAUDE.md` · `ralph/OPERATING_MANUAL.md` · `ralph/PROMPT.md`
- **Design:** `docs/ARCHITECTURE.md` · `docs/ARBITRAGE_THEORY.md` · `docs/TECH_STACK.md`
- **Guarantees:** `docs/DATA_INTEGRITY.md` · `docs/LATENCY.md` · `docs/TESTING_STRATEGY.md` · `docs/SECURITY.md`
- **Decisions & memory:** `ralph/memory/{progress,learnings,decisions,blocked}.md`

## Quick start (developers)

```bash
make setup          # uv sync (core + dev) + install pre-commit hooks
make check          # fast gate: lint + types + pairing + tests + coverage
make ci             # full gate incl. security audit
```

Optional tiers (need services / a chain):

```bash
make services-up    # local Postgres/Timescale + Redis (docker)
make integration    # RPC-fork / redis / db tiers
make verify         # on-chain data-integrity checks
make bench          # latency regression gates
```

Configuration is via environment variables (prefix `L2ARB__`); copy
`.env.example` to `.env` and fill in **read-only** RPC/WSS endpoints. There are no
secret keys in this project by design.

## Integrate the engine (any language)

The engine is a **plug-and-play calculation core**: your per-chain bots feed it
live pool state as JSON and it returns the ranked top-N opportunities. Two
transports share one contract — see **`docs/INTEGRATION.md`** for the full schema.

```bash
# 1) Subprocess (zero setup — any language pipes JSON):
echo "$REQUEST_JSON" | python -m l2arb.api.runner

# 2) Read-only HTTP:
uvicorn l2arb.api.http:app --port 8080   # POST /detect ; GET /health
```

In Python:

```python
from l2arb.engine.engine import ArbitrageEngine
from l2arb.detect.profit import GasModel, ProfitContext

engine = ArbitrageEngine(max_hops=4)
engine.configure_chain(42161, ProfitContext(GasModel(gas_price_wei=...).cost_fn(price_fn)))
engine.ingest_many(pool_states)          # from your chain bots (on-chain-verified)
top10 = engine.compute(top_n=10)         # ranked, risk-scored, net-profitable
snapshot = engine.snapshot()             # persist working memory; load_snapshot() to warm-start
```

## Running the build loop

```bash
make loop           # runs ralph/loop.sh — see ralph/OPERATING_MANUAL.md first
touch ralph/STOP    # graceful stop after the current iteration
```

Each iteration does exactly one backlog task, TDD-first, leaves the tree green,
records progress, and commits. Steer it by editing the **plan**
(`plan/backlog.md`) and the **constitution** (`CLAUDE.md`) — not by editing the
agent.

## Status

**The calculation core is feature-complete and fully tested** (M4 + M5): exact
integer AMM math across **four families — Uniswap V2 (constant-product), Uniswap
V3 (concentrated liquidity), Curve StableSwap, and Balancer weighted** — the rate
graph, all detectors (2-hop, triangular, cross-dex, bounded multi-hop, cross-chain
2-hop), the net-profit gate with MEV/front-running risk scoring, top-N ranking,
persistence + caching (in-process + Redis + snapshot/restore + a **Timescale/
Postgres opportunity store**), an **offline backtest replay + metrics** path, a
`numba`-JIT'd tropical min-plus hot loop, and the language-agnostic JSON
integration surface. `make check` is green with **100% line+branch coverage** and
property-based tests across the core; the security audit is clean.

**Real-onchain verification is live and bit-exact.** The `verify` tier reproduces
a live Base Uniswap **V2** and **V3** WETH/USDC pool's on-chain
`getAmountsOut`/`QuoterV2` output **to the wei** at a pinned block, fed through the
real ingestion boundary — plus an adversarial/stress tier that proves the engine
never fabricates an edge under hostile input (it caught and pinned a real Balancer
over-statement bug). Curve/Balancer live cross-checks (T-0308) and a live-fork
`chain` tier await node access (see `ralph/memory/blocked.md`).

Live ingestion (Phase 1/2 RPC/WSS) is intentionally external — per-chain bots feed
the engine through the ingestion boundary. See `ralph/memory/progress.md` for the
live log.

## Scope & safety

In scope: detection of the arbitrage types above from public on-chain data, plus
an offline backtesting/analytics path. **Out of scope (never built):** signing or
submitting transactions, private keys, and MEV *extraction* (sandwiching/front-
running). Those are refused and recorded in `ralph/memory/blocked.md`, never
implemented. See `CLAUDE.md` §1 and `ralph/memory/decisions.md` (ADR-001).
