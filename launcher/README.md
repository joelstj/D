# L2ArbBot launcher

The single entry point that **installs, wires, runs, and supervises** the merged
L2 arbitrage flash-loan bot. Stdlib-only Python (no third-party imports) so it
bundles cleanly into the Windows `.exe`.

## What it does

| Command | Behaviour |
|---------|-----------|
| `l2arb` (no args) | **auto**: install if needed, then run + open the dashboard |
| `l2arb doctor` | report toolchains, install state, config readiness |
| `l2arb install` | build all components (`--paper-only` for the dashboard alone) |
| `l2arb run` | start the stack (`--live` for the full on-chain path, else paper) |

It orchestrates three runnable services and serves the UI on one origin:

```
engine     127.0.0.1:8080   uvicorn l2arb.api.http:app     (POST /detect, /health)
ingestion  0.0.0.0:9001     l2-ingest --config config.toml (ws opportunity feed)
dashboard  127.0.0.1:8787   node backend/dist/index.js     (REST + /ws + served UI)
```

- **Paper mode (default, zero-config):** only the dashboard runs, with the
  simulated feed. Safe and instant — no RPC endpoints required.
- **Live mode (`--live`):** engine + ingestion + dashboard, with the dashboard
  consuming real detections over `ws://127.0.0.1:9001`. Requires a filled
  `.l2arb/config.toml` (real RPC endpoints + pool registries). Execution stays in
  **paper** mode regardless — broadcasting a real flash-loan transaction is a
  separate, human-authorised step, never initiated by the launcher.

## Dev usage (from a repo checkout)

```bash
cd launcher
python3 -m l2arb doctor        # check toolchains
python3 -m l2arb install       # build engine venv + dashboard + ingestion
python3 -m l2arb run           # paper mode, opens the dashboard
python3 -m l2arb run --live    # full stack (needs .l2arb/config.toml filled)
```

State (venv, generated `config.toml`, logs, install marker) lives under
`<workspace>/.l2arb/`. In a frozen `.exe` the workspace is a per-user install
dir; in a dev checkout it is the repo root. Override with `L2ARB_HOME`.

## Health monitor & self-heal

Once the stack is up, `run` hands off to a continuous **health monitor** that
redraws a compact HUD each tick:

```
  L2 Arb Bot — health monitor            uptime 00:03:12
  SERVICE     STATE       PID     UPTIME   PING    RESTARTS
  engine      ● healthy   12001   03:10    2ms     0
  ingestion   ● healthy   12002   03:09    1ms     0
  dashboard   ● healthy   12003   03:11    3ms     0
```

Each tick it probes every service — process liveness **plus** its HTTP `/health`
(engine `:8080`, ingestion `:9100` — the observability router's `metrics_bind`,
dashboard `:<port>/api/health`) — **self-diagnoses** any fault (exit code + last
log lines, or "up but not responding"), and **self-heals** by restarting a
crashed or wedged process with exponential backoff and a bounded restart budget.
A service that stays healthy long enough has its restart budget forgiven; one
that can't be recovered is isolated as `failed` while the others keep running.
Ctrl-C stops everything cleanly. When stdout isn't a TTY (logs/CI) the HUD
degrades to one-line state-change events.

Recovery restarts detection/UI **infrastructure only** — it never signs,
submits, re-broadcasts, or deploys anything. Execution stays paper-by-default
and human-gated. The decision logic (`l2arb/health.py`) is pure and unit-tested;
`tests/test_health_integration.py` exercises a real crash-and-restart against a
live child process.

## Tests

```bash
python3 -m unittest discover -s launcher/tests
```

## Building the `.exe`

See [`../scripts/`](../scripts/) and [`../docs/INSTALL.md`](../docs/INSTALL.md).
`run_launcher.py` is the PyInstaller entry point.
