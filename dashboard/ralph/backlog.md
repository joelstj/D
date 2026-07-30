# Backlog

Prioritized, top-to-bottom. The loop takes the highest unchecked `[ ]` item.
Keep items small enough to finish and verify in a single iteration. When you
discover follow-up work, add it here rather than expanding the task in flight.

## P0 — foundations & correctness

- [ ] Add a `GET /api/opportunities/:id` endpoint returning a single opportunity
      (404 when missing) and cover it with an API test.
- [x] Persist settings to disk (`backend/.data/settings.json`) so restarts keep
      the operator's configuration; load on boot, debounce writes on change.
      Done: `settings/persistence.ts` (load/save, `.partial()` schema so an
      older file still loads) + `server.ts` wiring (`store.onChange` saves;
      `executionMode` always re-seeds from `EXECUTION_MODE`, never resumes a
      stale persisted value — see root `CLAUDE.md` §2 invariant 3). Writes are
      synchronous on every settings change rather than debounced — settings
      patches are infrequent/user-driven, not a hot path, so this was judged
      simple-and-correct over premature batching; revisit if that assumption
      stops holding. Tests: `test/persistence.test.ts`, `test/settingsRestart.test.ts`.
- [ ] Add request logging + a global Express error handler that returns a
      consistent `{ error, message }` shape; test the 404 and error paths.
- [ ] Add a `/api/stats/history` endpoint backed by an in-memory ring buffer of
      recent stats snapshots; stream it into the frontend PnL/activity chart.

## P1 — live data (real DEX quoting)

- [ ] Implement Uniswap v3 QuoterV2 reads in `LiveProvider` for one pair on Base
      (USDC/WETH): fetch a real quote via viem and log it. Add a unit test with a
      mocked transport.
- [ ] Add a pool/venue registry (addresses per network/DEX) under
      `backend/src/arbitrage/venues/` and wire the live provider to iterate it.
- [ ] Compute real two-leg spreads from live quotes and emit genuine
      opportunities in `live` mode (still paper execution).

## P1 — frontend depth

- [ ] Replace the activity sparkline with a proper PnL-over-time area chart fed
      by `/api/stats/history`, following the dataviz palette + interaction rules.
- [ ] Add a per-opportunity detail drawer (full route, fees breakdown, quote
      freshness) opened from the table row.
- [ ] Add a light/dark theme toggle that persists to `localStorage` and stamps
      `data-theme` on `<html>`.

## P2 — live execution (gated, opt-in)

- [ ] Define an on-chain `IFlashLoanArb` contract interface + a
      `contracts/README.md` documenting the required audited deployment. Do NOT
      enable broadcasting.
- [ ] In `LiveExecutor`, build (but do not send) the transaction and run an
      `eth_call` preflight through the connected signer; surface the simulated
      result. Keep an explicit env+settings double-gate before any real send.

## P2 — platform

- [ ] Add API-key auth for mutating endpoints (`PUT/PATCH /settings`, `execute`)
      with a dev-mode bypass; document it in the OpenAPI spec.
- [ ] Expand the Python SDK with a WebSocket streaming client and an example
      arbitrage-watcher script.
- [ ] Add a Playwright end-to-end test that boots both servers, asserts an
      opportunity appears, toggles a setting, and confirms it round-trips.
- [ ] Add a GitHub Actions matrix that runs `pnpm verify` on Node 20 and 22.

## Done

Seeded at harness creation — see `progress.md` for detail.

- [x] Monorepo scaffold (pnpm workspaces), env config, licensing.
- [x] Backend: settings store + schema, simulated provider, paper/live executor
      split, engine, REST + WS API, OpenAPI spec, 21 passing tests.
- [x] Frontend: live dashboard, MetaMask via wagmi, wired settings panel,
      opportunities table, stats tiles, executions log, 15 passing tests.
- [x] Ralph harness: prompt, spec, backlog, guardrails, loop runner, verify gate.
