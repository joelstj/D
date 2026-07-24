# Architecture

> Ports & adapters (hexagonal). The **core** — AMM math and cycle detection — is
> pure Python that knows nothing about web3, redis, or HTTP. Everything external
> plugs in behind a typed `Protocol`. This is what makes "add a new chain / DEX"
> an *adapter*, not a rewrite, and what makes the core exhaustively unit-testable
> without a network.

## 1. Layering

```
                         ┌──────────────────────────────────────────┐
   external world  ──▶   │  ADAPTERS (impure, I/O, swappable)        │
                         │  rpc/  dex/  store/  oracle/  api/  cex/   │
                         └───────────────┬──────────────────────────┘
                                         │  typed Protocols (ports)
                         ┌───────────────▼──────────────────────────┐
                         │  APPLICATION (orchestration, async)       │
                         │  engine/  stream/  pipeline/  verify/     │
                         └───────────────┬──────────────────────────┘
                                         │  plain data types
                         ┌───────────────▼──────────────────────────┐
                         │  CORE (pure, deterministic, no I/O)       │
                         │  amm/  graph/  detect/  profit/  model/   │
                         └──────────────────────────────────────────┘
```

Dependencies point **inward only**. `core` imports nothing from `adapters`.
`adapters` implement ports declared next to `core`/`application`. Enforced by an
import-linter contract in CI (a task in Phase 0).

## 2. Proposed package layout (`src/l2arb/`)

```
l2arb/
  config.py            # pydantic-settings; all tunables; no secrets in code
  logging.py           # structlog setup (JSON in prod, dev renderer locally)
  types.py             # value objects: Token, PoolId, Quote, Rate, Blockstamp
  errors.py            # typed exception hierarchy (DataError vs InfraError)

  model/               # CORE — domain value objects & units
    token.py           # Token(address, decimals, chain) canonicalization
    pool.py            # Pool state snapshots (V2Reserves, V3Slot0, ...)
    opportunity.py     # Opportunity + provenance record

  amm/                 # CORE — pure pricing math (no I/O, integer-exact)
    constant_product.py
    concentrated_liquidity.py
    stableswap.py      # Curve StableSwap 2-coin (delivered)
    weighted.py        # Balancer weighted 2-token (delivered)
    quote.py           # uniform marginal_rate/amount_out dispatch over PoolState
    sizing.py          # optimal-size solvers (closed form + memoized golden section)

  graph/               # CORE — exchange-rate graph
    rategraph.py       # build/update; -ln(rate) edges; dirty tracking
    negcycle.py        # Bellman-Ford / SPFA
    tropical.py        # min-plus K-hop matrix sweep (numba)

  detect/              # CORE — detectors compose graph + amm + profit
    two_hop.py
    triangular.py
    multi_hop.py
    cross_chain.py     # simple 2-hop across chains
    profit.py          # net-profit gate (fees + gas + slippage + bridge)

  ports/               # typed Protocols (the seams)
    rpc.py             # ChainClient: call, get_logs, subscribe, head
    dex.py             # DexAdapter: discover pools, decode state, price
    store.py           # OpportunityStore, SnapshotStore
    oracle.py          # VerificationOracle: independent re-read of state
    stream.py          # OpportunitySink

  rpc/                 # ADAPTER — async multi-endpoint web3/JSON-RPC
    client.py          # failover, retry (tenacity), rate limit
    subscriptions.py   # newHeads / logs over WSS; reconnect
    multicall.py       # batch eth_call for cold-start state loads
    reorg.py           # head tracking + reorg detection/invalidation

  dex/                 # ADAPTER — concrete DEX families implementing DexAdapter
    uniswap_v2.py
    uniswap_v3.py
    registry.py        # factory-event discovery + curated token lists

  oracle/              # ADAPTER — Blockscout/second-RPC verification
    blockscout.py      # read_contract at pinned block; contract/abi checks
    crosscheck.py      # two-source agreement logic

  store/               # ADAPTER — persistence
    redis_cache.py     # hot pool state
    pg_store.py        # opportunities + snapshots (SQLAlchemy async)
    migrations/        # alembic

  cex/                 # ADAPTER — CCXT reference prices (optional, offline sanity)
    ccxt_reference.py

  engine/              # APPLICATION — wires ports; owns the run loop
    engine.py          # cold start -> subscribe -> detect -> verify -> emit
    dirty.py           # per-block dirty-set propagation to detectors

  stream/              # APPLICATION — outbound
    api.py             # FastAPI read-only endpoints
    ws.py              # opportunity WebSocket feed
    metrics.py         # prometheus counters/histograms (latency SLOs)

  verify/              # APPLICATION — continuous data-integrity subsystem
    verifier.py        # sample pools -> oracle re-read -> assert agreement
    freshness.py       # staleness guards

  backtest/            # APPLICATION — offline research (Phase 9, never runtime)
    replay.py          # historical snapshot replay
    metrics.py         # quantstats/statsmodels reports
    allocation.py      # PyPortfolioOpt capital allocation analytics
```

## 3. Data flow (streaming hot path)

```
WSS newHeads/logs ─▶ decode (orjson) ─▶ update RateGraph edges (O(1))
      ─▶ mark dirty tokens ─▶ incremental negcycle search over dirty subgraph
      ─▶ exact re-price at optimal size (amm/sizing) ─▶ net-profit gate
      ─▶ opportunity ─▶ [async] verify against oracle ─▶ emit to sink + store
```

- Everything on this path is `async` and non-blocking; CPU-heavy math is offloaded
  to `numba`-compiled functions that release the GIL where possible.
- Verification is **out-of-band**: an opportunity is emitted immediately with a
  `provenance` record and *concurrently* re-verified; a failed verification
  retracts/flags it. This keeps latency low without trusting unverified data.

## 4. Concurrency model

- One asyncio task per chain subscription; a bounded `asyncio.Queue` decouples
  ingest from detection so a burst of logs never blocks the socket.
- Backpressure: if detection falls behind, coalesce updates per pool (keep only
  the latest reserves) — we care about *current* state, not every intermediate.
- No shared mutable state across chains except the read-only canonical-asset map.

## 5. Extension points (evolvability contract)

Adding a **new L2**: implement/configure a `ChainClient` endpoint set; register
its DEX deployments and token list. No core change.

Adding a **new DEX family**: implement `DexAdapter` (discover, decode state,
marginal rate, exact quote) + its AMM math module + tests. No detector change —
detectors consume rates and exact-quote callables, not DEX specifics.

Adding a **new detector**: consume `RateGraph` + `profit` gate; register with the
engine's dirty-set dispatch. No adapter change.

Every such addition is recorded as an ADR in `ralph/memory/decisions.md`.
