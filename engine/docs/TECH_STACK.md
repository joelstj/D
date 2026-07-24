# Tech Stack — choices & rationale

> The brief supplied a large quant/TradFi toolbox (CCXT, backtrader,
> arbitragelab, PyPortfolioOpt, yfinance, Alpaca, vectorbt, …). Most of that
> stack is built for **centralized-exchange / TradFi** workflows. On-chain L2 DEX
> arbitrage detection is a **Web3 systems** problem first (RPC, AMM math, mempool/
> log streaming) and a quant problem second. This document maps every requested
> tool to where it genuinely fits, and names the Web3 pieces the brief didn't
> list but the engine can't work without. Honesty here prevents building the
> wrong thing.

## 1. Core runtime (the engine cannot exist without these)

| Concern | Choice | Why |
|---|---|---|
| L2 state & events | **web3.py (async)** + raw `eth_call` via `eth-abi` | read reserves/slot0/ticks, decode logs; raw calls for hot paths |
| Streaming | **websockets / aiohttp** | `eth_subscribe` newHeads + logs, low latency |
| Retry/resilience | **tenacity** | backoff/failover on flaky RPC |
| Fast JSON | **orjson** | WS decode is on the hot path |
| Numerics | **numpy + numba** | integer-exact/vectorized AMM & min-plus cycle math, JIT hot loops |
| Columnar data | **polars** | fast snapshotting/analytics without pandas overhead |
| Config/validation | **pydantic + pydantic-settings** | typed config, validate every external input |
| Logging | **structlog** | structured, greppable, no secret leakage |
| Hot cache | **redis (async)** | sub-ms pool-state cache |
| Store | **SQLAlchemy 2 + asyncpg + TimescaleDB** + **alembic** | opportunity/snapshot time-series + migrations |
| Service | **FastAPI + uvicorn** | read-only opportunity API + WS feed |
| Metrics | **prometheus-client** | latency histograms, SLO gauges |
| Batch reads | **multicall** (or web3 batch) | cold-start state load in one round-trip |
| Graph (reference) | **networkx** | validate custom detectors against a reference impl in tests |

> **Not on the brief, but mandatory** — the entire first column above except
> where noted. The brief's data-source list (Polygon, Alpaca, yfinance, Finnhub,
> IEX, …) is TradFi/CEX and is **not** used in the on-chain detection path (see
> `docs/DATA_INTEGRITY.md §8`).

## 2. Requested quant stack — where each piece actually goes

| Requested tool | Fits? | Where |
|---|---|---|
| **CCXT** ⭐ | ✅ (bounded) | `cex/ccxt_reference.py` — *optional* CEX reference prices & CEX↔DEX spread report; **never** the live on-chain path |
| **backtrader** | ✅ | `backtest/replay.py` — event-driven historical replay of detected opportunities |
| **PyPortfolioOpt** | ✅ | `backtest/allocation.py` — capital-allocation analytics across concurrent opportunities (offline) |
| **arbitragelab** (hudson-and-thames) | ⚠️ optional | statistical-arb *research* only; **heavy + historically license-gated**; keep out of runtime, add only if license permits |
| **quantstats** | ✅ | backtest performance/tearsheet reports |
| **scikit-learn / statsmodels / arch** | ✅ | offline opportunity analysis (decay models, volatility of spreads) |
| **matplotlib / plotly / mplfinance / finplot** | ✅ | reporting/visualization of backtests (offline) |
| **uv** ⭐ | ✅ | package/venv manager (project standard) |
| **ruff** ⭐ | ✅ | lint + format (syntax/style tier) |
| **pandas / numpy / polars** ⭐ | ✅ | core numerics (numpy/polars hot, pandas for quant interop) |
| **yfinance, Alpaca, Polygon, Tiingo, Quandl, Finnhub, IEX, Alpha Vantage, CoinGecko, EOD, FMP, Marketstack** | ❌ for detection | TradFi/CEX/aggregated data — not on-chain-verifiable; excluded from runtime. CoinGecko/CEX may appear only as labelled offline references |
| **vectorbt, bt, zipline, PyAlgoTrade, QSTrader, fastquant, QuantConnect/Lean, finmarketpy** | ➖ not adopted | overlap with backtrader; adopt only if a concrete need appears (ADR required) |
| **IBKR / Alpaca / Oanda / Kraken / Coinbase execution APIs** | ❌ out of scope | execution/brokerage — this engine never trades |
| **TA-Lib, QuantLib, mlfinlab** | ➖ optional | only if a specific analytic needs them (ADR) |
| **cursor / VSCode / JupyterLab / poetry** | ✅ editor/env | developer tooling; project standardizes on **uv** (not poetry) for reproducible locks |

## 3. Web3 stack the brief omitted (must-add, with reasons)

- **web3.py / eth-abi / eth-utils** — there is no on-chain arbitrage without
  reading contract state and decoding logs.
- **Anvil (Foundry)** — deterministic **fork testing** at pinned blocks; the
  backbone of the `chain` test tier. (`curl -L https://foundry.paradigm.xyz | bash`)
- **Blockscout MCP** — the independent **verification oracle**
  (`read_contract`, `get_contract_abi`, `get_address_info`). Central to
  `docs/DATA_INTEGRITY.md`.
- **Uniswap V2/V3 (and forks) ABIs & QuoterV2** — the pools we price and the
  on-chain oracle we validate local math against.

## 4. Why uv (not poetry) and ruff

- **uv**: fast, reproducible resolver + lockfile; single tool for venv, sync,
  and running. `poetry` is fine but we standardize on one; uv wins on speed and
  lockfile determinism (matters for the "verifiable/reproducible" requirement).
- **ruff**: format + lint + import-sort + many security lints in one fast pass —
  it *is* the syntax/style tier.

## 5. Language-boundary escape hatch

If profiled Python cannot hold the latency SLO for cycle search (Phase 6), the
plan permits a **Rust (pyo3/maturin)** implementation of `graph/negcycle` behind
the existing port — opt-in, same interface, same tests. Recorded as an ADR before
adoption. Python remains the default and the reference implementation.

## 6. Versioning & reproducibility

- Dependencies are locked with `uv.lock` (committed). Runtime and analytics deps
  are separated (`[project.optional-dependencies]`) so the low-latency image
  stays lean.
- `pip-audit` runs in CI; a vulnerable transitive dep fails the build.
