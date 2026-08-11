<div align="center">

# ⚡ L2 Arbitrage Flash-Loan Bot

**Four components, one product.** A Rust ingestion layer, a Python detection
engine, Solidity flash-loan contracts, and a React dashboard — merged, wired
end-to-end, and packaged behind a single launcher and a self-bootstrapping
Windows `.exe`.

</div>

> [!IMPORTANT]
> **Safe by default.** Ships in **paper / simulation mode** — opportunities are
> detected from real on-chain state but **no transactions are broadcast**. Live
> execution is deliberately gated and human-authorised. Flash-loan arbitrage
> carries real financial and smart-contract risk. Nothing here is financial advice.

---

## What it does

Reads live DEX state across five L2s → detects arbitrage → ranks it by net profit
after gas / flash-loan / bridge costs → streams it to a dashboard → (optionally,
and only when a human authorises it) executes it atomically via audited
flash-loan contracts that **revert unless profit ≥ your minimum**.

```
 ingestion (Rust)            engine (Python)          dashboard (Node + React)
 reads 5 L2s over WS/RPC  ─▶ detects 2-hop /       ─▶ live opportunities,
 (Arbitrum, Base,            triangular /             wired settings, KPIs,
 Optimism, Unichain, Ink)    multi-hop / x-chain      MetaMask, paper/live split
        │  POST /detect :8080        │  ranked opps            │  POST /api/execute/:id
        └────────────────────────────┘                         ▼  (paper by default)
        └──── ws :9001 opportunity feed ───▶ dashboard    contracts (Solidity)
                                              backend      atomic flash-loan executor
                                              (ExternalProvider)   — gated, human-only
```

See **[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)** for the full data flow and
the three integration seams.

An end-to-end **latency-health** system times the trip from ingestion read to
on-screen opportunity and breaks it down per component (2–5 stages each), so
bottlenecks are visible in the dashboard's *Pipeline Latency* HUD
(`GET /api/latency`). A separate, strictly read-only probe reports on-chain
execution readiness (`GET /api/health/execution`) without ever broadcasting. See
**[`docs/LATENCY.md`](docs/LATENCY.md)**.

The dashboard also has a **Contracts** panel (`/api/contracts/*`): a per-chain
*compile → deploy → monitor* status board, one-click **compile** (server-side
Hardhat) and **deploy** (the unsigned deployment is **signed in your MetaMask** —
the backend holds no key and never broadcasts), automatic recording of the
deployed address into `.env` + `contracts/deployments/`, and a strictly read-only
multi-chain **readiness sweep** (`getCode` + an `aavePremiumBps()` staticCall) —
also runnable headless via `node scripts/contract_stress_test.mjs`. Profit from
every successful trade is forwarded straight to the executing wallet; the contract
retains no funds.

## Quick start

### Option A — the launcher (recommended, cross-platform)

```bash
cd launcher
python3 -m l2arb doctor      # check toolchains (Python 3.11/3.12, Node 20+, Rust 1.94+)
python3 -m l2arb run         # paper mode: builds the dashboard if needed, opens it
```

`python3 -m l2arb run` with no config launches the **paper dashboard** at
<http://localhost:8787> — real detection wiring, simulated fills, zero setup. For
the full live stack you don't need to know what to configure in advance: **every
launch runs a full health check** over each RPC endpoint, WebSocket URL, API key
and wallet address the stack needs, scores it out of 100%, and walks you through
anything missing — explaining what each value is, where to get it, and what a
correct one looks like, with a box to paste it into. Answers are validated and
endpoints are *proved* with a real `eth_chainId` call, then stored in a local
SQLite database (`.l2arb/credentials.db`) so you're asked once:

```bash
python3 -m l2arb install     # build engine venv + dashboard + ingestion binary
python3 -m l2arb setup       # guided: prompts for everything still missing
python3 -m l2arb health      # just the report (exit 0 when at 100%)
python3 -m l2arb run --live  # engine + ingestion + dashboard on real data
```

In practice that is usually **one paste**: the WebSocket URL is derived from the
HTTPS one, and token/DEX/pool addresses are never prompted for — they're
on-chain facts, shipped verified and re-proven by the startup gate. **No private
key is ever requested**; the only wallet value collected is your public address,
for profit to be paid to.

(Prefer to hand-edit? `.l2arb/config.toml` is the full surface — real RPC
endpoints + pool registries per chain, using `ingestion/config/config.example.toml`
as the reference. Endpoints accept a comma-separated `primary, backup` for
automatic rate-limit failover.)

**Configuration — one master `.env`.** Everything env-based lives in a **single
`.env` at the repo root** (`cp .env.example .env`): dashboard ports/mode, the
engine's optional tuning and its **Blockscout API key** (`L2ARB__BLOCKSCOUT__API_KEY`
— endpoints are built in, so the key is all you supply), and the human-gated
contracts deploy section. Every component reads it automatically; a component-local
`.env` overrides it, and real environment variables override both. (The Rust
ingestion layer is the one exception — it's configured by `config.toml` above, not
env vars.)

Once running, `l2arb run` shows a live **health HUD** and continuously
self-heals: it probes each service's process + `/health`, diagnoses faults, and
restarts a crashed or wedged one (with backoff) — infrastructure only, never the
human-gated execution path. Ctrl-C stops the stack cleanly.

### Option B — the Windows `.exe`

A single **`L2ArbBot.exe`** — **double-click it** and it installs everything on
first run, then just launches and opens the dashboard (safe paper mode) on later
runs. To go live, `L2ArbBot.exe setup` and paste one RPC URL.

Don't have the `.exe` yet? On a Windows machine, double-click
**[`Build_L2ArbBot.bat`](Build_L2ArbBot.bat)** in the repo root — it builds
`launcher\dist\L2ArbBot.exe` for you (no PowerShell flags to remember). Or build
it via the `build-windows-exe` GitHub Actions workflow. See
**[`docs/INSTALL.md`](docs/INSTALL.md)**.

For the complete reference — every API key/credential the system can use,
which ones you actually need, and how to run it day to day — see
**[`docs/OPERATIONS.md`](docs/OPERATIONS.md)**.

### Option C — Docker

```bash
cp ingestion/config/config.example.toml ingestion/config.toml   # then fill endpoints
docker compose up --build
# open http://localhost:8080
```

## Repository layout

| Path | What | Build / test |
|------|------|--------------|
| [`engine/`](engine/) | Python `l2arb` detection engine (FastAPI `/detect`) | `make check` |
| [`ingestion/`](ingestion/) | Rust `l2-ingest` data feed (5 L2s → engine → ws) | `cargo test --workspace` |
| [`contracts/`](contracts/) | Solidity flash-loan executor (profit-or-revert) | `bash scripts/verify.sh` |
| [`dashboard/`](dashboard/) | Node backend + React UI (REST + WebSocket) | `pnpm verify` |
| [`launcher/`](launcher/) | Stdlib-Python installer / launcher / self-healing supervisor (health HUD) | `python3 -m unittest discover -s launcher/tests` |
| [`scripts/`](scripts/) | `.exe` build (PyInstaller spec + drivers) | — |
| [`docs/`](docs/) | Architecture, install, and operations guides | — |

Each component keeps its own README and `CLAUDE.md`/`AGENTS.md` for internals.

**Integration smoke test.** `cd engine && uv run python ../scripts/e2e_smoke.py`
wires all four seams together on live data — it reads real Arbitrum pool state,
runs the engine's detection, streams the result through the ingestion WebSocket
envelope into the dashboard's `ExternalProvider`, and exercises the REST/UI
controls end to end (settings, one-click **paper** execute, and the
`LiveExecutor` broadcast refusal). It skips cleanly when the dashboard isn't
built or there's no outbound RPC.

## Safety

- **Paper by default.** `EXECUTION_MODE=paper`, `autoExecute=false`; the live
  executor refuses to broadcast.
- **Detection holds no keys.** The engine signs nothing and submits nothing.
- **Real data only.** Every number is derived from live on-chain state and
  block-stamped; nothing synthetic reaches a runtime path. The dashboard's
  opportunity mapper never fabricates prices.
- **Live execution is double-gated** and targets audited, atomic contracts that
  revert below your `minProfit`. Broadcasting is a separate, human-authorised
  step — never initiated by the bot.

See **[`CLAUDE.md`](CLAUDE.md) §2** for the full, binding invariant list.

## License

[MIT](LICENSE). Each component retains its own license (see its subdirectory).
