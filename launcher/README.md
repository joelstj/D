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

## Tests

```bash
python3 -m unittest discover -s launcher/tests
```

## Building the `.exe`

See [`../scripts/`](../scripts/) and [`../docs/INSTALL.md`](../docs/INSTALL.md).
`run_launcher.py` is the PyInstaller entry point.
