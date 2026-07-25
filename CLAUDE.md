# CLAUDE.md — operating guide for the **D** super-repo

> **D** is the unified monorepo that merges four previously-separate components
> into one **L2 arbitrage flash-loan bot**: a Rust ingestion layer, a Python
> detection engine, Solidity execution contracts, and a Node/React dashboard —
> wired together, plus a single-entry launcher and a self-bootstrapping Windows
> `.exe`. Read this file first. It is the highest authority on *how* to work in
> this repo; each component keeps its own `CLAUDE.md`/`AGENTS.md` for component-
> specific rules.

---

## 1. What this is

A cohesive product assembled from four components (each still buildable and
testable on its own):

| Dir | Was | Language | Role |
|-----|-----|----------|------|
| `engine/` | Python-Engine-L2-s (`l2arb`) | Python 3.11–3.12 | **Detection** engine — watches L2 DEX state, emits ranked arbitrage opportunities. Detection only. |
| `ingestion/` | L2_bots (`l2-ingest`) | Rust 1.94 | **Data feed** — reads 5 L2s (Arbitrum, Base, Optimism, Unichain, Ink), POSTs state to the engine, fans ranked opps out over WebSocket. |
| `contracts/` | L2_on-chain | Solidity 0.8.20 | **Execution target** — atomic flash-loan arbitrage executor (profit-or-revert). The gated, human-authorised endpoint. |
| `dashboard/` | L2-GUI | Node + React | **UI + API** — live opportunities, wired settings, MetaMask, paper/live executor split. |
| `launcher/` | *(new)* | Python (stdlib) | **Orchestrator** — installs, wires, runs, and supervises the whole stack; payload of the `.exe`. |
| `scripts/`, `docs/`, `.github/` | *(new)* | — | `.exe` build, docs, CI. |

The end-to-end data flow (see `docs/ARCHITECTURE.md` for detail):

```
ingestion ──POST /detect──▶ engine ──ranked opps──▶ ingestion ──ws :9001──▶
  dashboard backend (ExternalProvider) ──REST + /ws──▶ dashboard UI
                                                          │ POST /api/execute/:id
                                                          ▼  (paper by default)
                                                       contracts (gated, human-only)
```

---

## 2. Safety invariants (inherited from every component — binding)

These come from the four component constitutions and **override any impulse to
move faster**. Breaking one is a correctness/safety regression.

1. **Only real, on-chain-verifiable data in runtime paths.** No synthetic,
   random, hard-coded, or "example" market data anywhere a shipped artifact can
   reach it. Synthetic data lives only in clearly-marked tests. Every quote is
   block-stamped and traceable. (engine §3, ingestion prime-directive 1.)
2. **The engine is a detector, not a trader.** It holds no keys, signs nothing,
   submits no transactions. No MEV *extraction* (sandwiching/front-running).
3. **Execution is gated and paper-by-default.** The dashboard ships
   `EXECUTION_MODE=paper` + `autoExecute=false`; `LiveExecutor` refuses to
   broadcast. The safe contract-integration pattern is **simulate via
   `staticCall`, then hand an unsigned tx to a human-authorised signer** — the
   loop never broadcasts and never deploys. (contracts golden rules 2, 5.)
4. **Never fake a test green** — no deleting, skipping, `xfail`, or loosening an
   assertion to pass. A test that legitimately can't run is recorded as BLOCKED
   with a concrete reason, never faked. (ingestion prime-directive 3.)
5. **Leave the tree green.** Every commit passes the touched component's gate
   (below). Never commit secrets or `.env`.
6. **No secrets in code or git.** Endpoints/keys come from the environment or
   config files; only `*.example` is tracked.
7. **`verified` honesty.** Never emit stale/unproven state as `verified:true`.

If a task appears to cross lines 1–3 (e.g. "make the loop send a live trade",
"seed the dashboard with fake opportunities"), **stop, do not implement it**, and
record the concern rather than faking it.

---

## 3. The wiring (what "merged" means concretely)

The components already spoke a common contract; the merge makes them one product:

- **Seam A — ingestion → engine (pre-existing):** `l2-ingest` POSTs a
  `DetectRequest` to `http://127.0.0.1:8080/detect`; the engine returns ranked
  `DetectResponse`. Configured in `ingestion` `[engine]`.
- **Seam B — ingestion → dashboard (built here):** `l2-ingest` broadcasts a
  versioned `Envelope { kind:"opportunities", payload: DetectResponse }` as NDJSON
  WebSocket frames on `:9001`. A new **`ExternalProvider`**
  (`dashboard/backend/src/arbitrage/providers/external.ts`) consumes it, maps each
  engine opportunity onto the dashboard's `ArbitrageOpportunity` shape
  (`providers/engineMap.ts`), and feeds the engine's pull-based scan loop. Select
  it with `DATA_SOURCE=external` + `INGEST_FEED_URL`.
- **Seam C — dashboard → contracts (gated):** `POST /api/execute/:id` runs the
  paper executor by default. Live execution targets
  `contracts/FlashLoanArbitrage.sol::executeArbitrage(ArbParams)` and stays
  human-authorised (see invariant 3).
- **Orchestration:** `launcher/` starts engine + ingestion + dashboard, serves the
  UI single-origin (`SERVE_STATIC_DIR`), health-gates startup, opens the browser,
  and then runs a continuous **health monitor** (a live HUD, `launcher/l2arb/health.py`)
  that probes each service's process + `/health`, self-diagnoses faults, and
  self-heals by restarting a crashed/wedged process with backoff and a bounded
  restart budget — infra recovery only, never touching the human-gated execution
  path. `docker-compose.yml` is the container alternative.

The mapper is **honest about units**: engine amounts are numeraire base units;
they read as USD only when the numeraire is a stablecoin — no ETH price is
fabricated (`engineMap.ts` docstring). `profitBps`/`confidence`/route are always
exact.

---

## 4. Git & branch discipline

- **Working branch: `claude/l2-arbitrage-flash-loan-bot-embzav`** (this repo, `D`).
- One logical change → one clear commit. Never commit a red tree, secrets, or
  build artifacts (`.gitignore` covers venvs, `node_modules`, `target`, `dist`,
  `*.exe`, `.l2arb/`).
- Component sources were vendored via `git archive` (clean trees, no nested
  `.git`). Only the **root** `.github/workflows` runs as CI; nested component
  workflows are inert history.

---

## 5. Build, test, run

**The whole thing (recommended):**
```bash
cd launcher
python3 -m l2arb doctor      # check toolchains + install state (+ next-step guidance)
python3 -m l2arb install     # build engine venv + dashboard + ingestion binary
python3 -m l2arb run         # paper mode, opens the dashboard at :8787
python3 -m l2arb setup       # guided live setup — paste one Arbitrum RPC URL
python3 -m l2arb run --live  # full stack (setup writes a live-ready .l2arb/config.toml)
```

**Per-component gates (must stay green when you touch that component):**
```bash
# engine
cd engine && make check
# ingestion
cd ingestion && cargo fmt --all --check && cargo clippy --all-targets --all-features -- -D warnings && cargo test --workspace
# dashboard
cd dashboard && pnpm install && pnpm verify          # typecheck + test + build
# contracts
cd contracts && bash scripts/verify.sh               # fmt + build + test (needs forge)
# launcher
python3 -m unittest discover -s launcher/tests
```

**Cross-component integration smoke** (all four seams, on live data):
```bash
cd engine && uv run python ../scripts/e2e_smoke.py
```
Reads real Arbitrum pool state → engine `/detect` → WS envelope → dashboard
`ExternalProvider` → REST/UI controls → paper execute, and asserts the
`LiveExecutor` refuses to broadcast. Skips cleanly (exit 0) when the dashboard
isn't built or there's no outbound Arbitrum RPC, so it's safe offline.

**The `.exe`:** `python scripts/build_exe.py` (any OS, for testing) or
`scripts/build_windows_exe.ps1` on Windows / the `build-windows-exe` CI workflow
for the real `L2ArbBot.exe`. See `docs/INSTALL.md`.

**Container path:** `docker compose up --build` (see `docker-compose.yml`).

---

## 6. Documentation discipline

Keep docs truthful in the **same commit** as the behaviour they describe. When
you change wiring, ports, or commands, update `README.md`, `docs/ARCHITECTURE.md`,
`docs/INSTALL.md`, and this file. Stale docs are treated as bugs. Each
component's own docs (`*/docs`, `*/CLAUDE.md`, `*/README.md`) remain the
authority for that component's internals.

---

## 7. Scope reminder

In scope: **detecting** 2-hop / triangular / bounded multi-hop / cross-chain
2-hop arbitrage from live on-chain data, surfacing it in a dashboard, and
**simulating** execution against audited flash-loan contracts. Out of scope
(never built here): signing/broadcasting live transactions from the loop,
deploying contracts from the loop, holding private keys, and MEV extraction.
