# Product spec — L2 Arbitrage GUI

## Vision

A plug-and-play, production-grade dashboard for **Layer-2 flash-loan arbitrage**.
It shows arbitrage opportunities in real time, lets the operator dial in every
strategy and risk parameter with changes wired straight to the backend, connects
to MetaMask, and exposes a language-agnostic API so any application or language
can integrate. Beautiful, intuitive, fast, and safe by default.

## Architecture (already scaffolded)

- **backend/** — Node + TypeScript. Express REST + `ws` WebSocket. A pluggable
  arbitrage engine (`simulated` and `live` opportunity providers), a validated
  settings store with live reload, and a paper/live executor split (live is
  gated). OpenAPI contract in `backend/openapi.yaml`.
- **frontend/** — React + TypeScript + Vite + Tailwind v4 + wagmi/viem. Live
  opportunity table, KPI tiles, PnL/activity sparkline, a fully-wired settings
  panel, MetaMask connect + network switch, execution activity log.
- **sdk/** — thin clients (JS, Python) over the same REST/WS surface.
- **ralph/** — this autonomous build harness.

## Principles

1. **Every control is wired.** A setting changed in the UI (or via API) validates
   on the backend and takes effect on the next scan — no restart, no redeploy.
2. **Safe by default.** `executionMode=paper` and `autoExecute=false` out of the
   box. Live execution requires deliberate configuration and stays gated behind
   `LiveExecutor`. Never risk funds implicitly.
3. **Language-agnostic.** The GUI has no privileged backdoor; everything it does
   is a documented REST/WS call any client can make.
4. **Real-time.** Opportunities, stats, executions, and settings changes stream
   over WebSocket to every connected client.
5. **Verified.** Typecheck + tests + build are green at every commit.

## Acceptance bar for "done" on any task

- Types check (`pnpm typecheck`), tests pass (`pnpm test`), build succeeds
  (`pnpm build`) — all via `bash ralph/verify.sh`.
- New behavior has tests that would fail without the change.
- No secrets committed. No live-trade path enabled without an explicit task.
- UI changes are responsive (mobile + desktop) and keep light/dark legibility.

## Roadmap themes (turned into concrete items in `backlog.md`)

- **Live data**: real DEX quoting for the `live` provider (Uniswap v3 QuoterV2,
  Aerodrome/Camelot/Velodrome), multi-hop routing, real gas via viem.
- **Live execution (gated)**: audited flash-loan contract interface, signer
  wiring via the connected wallet, simulation/`eth_call` preflight, MEV-aware
  submission — all opt-in and clearly fenced.
- **Persistence**: settings + execution history in a store (SQLite/Postgres).
- **Auth & multi-user**: API keys / sessions for the control surface.
- **Observability**: structured metrics, PnL history charts, alerting.
- **SDKs & docs**: expand JS/Python clients, add examples in more languages.
- **Testing**: end-to-end (Playwright) and load tests for the WS fan-out.
