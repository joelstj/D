# Progress log

Newest last. One line per completed unit of work: `YYYY-MM-DD — area: what & why`.
This is the loop's long-term memory across fresh-context iterations.

- 2026-07-18 — scaffold: pnpm monorepo, env config (.env.example), MIT license.
- 2026-07-18 — backend: validated settings store + zod schema with live-reload
  change events; the mechanism that makes every control take effect immediately.
- 2026-07-18 — backend: pluggable arbitrage engine with simulated provider (lively
  but realistic spreads) and a live provider skeleton that connects real RPCs.
- 2026-07-18 — backend: paper/live executor split; live execution gated. Engine
  resolves the executor from live settings so execution mode is wired end-to-end.
- 2026-07-18 — backend: Express REST + ws WebSocket fan-out (snapshot on connect,
  opportunity/stats/execution/settings/alert streams); OpenAPI contract; 21 tests.
- 2026-07-18 — frontend: React+wagmi dashboard — MetaMask connect/switch, live
  opportunities table, KPI tiles + sparkline, wired settings panel, executions
  log, alerts; 15 tests. Verified end-to-end with a Playwright screenshot pass.
- 2026-07-18 — ralph: harness authored (PROMPT, SPEC, backlog, AGENT rules,
  verify gate, loop runner). Ready to iterate toward the roadmap in SPEC.md.
