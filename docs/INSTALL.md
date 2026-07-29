# Install & run

> **Looking for the complete API-key/credential reference, or how to operate
> the bot day to day?** See **[`docs/OPERATIONS.md`](OPERATIONS.md)** — this
> page is the quickstart; that one is the detailed reference.

**In a hurry?** On Windows: get `L2ArbBot.exe` (below), **double-click it**, wait
for the one-time setup, and the dashboard opens in your browser. That's it — it
starts in **safe paper mode** (it simulates; it never sends a real transaction or
touches your funds). To watch **real** on-chain data later, run one command:
`L2ArbBot.exe setup` and paste an RPC URL.

Everything below is the detail, from easiest to most manual.

---

## 1. The Windows app (a few clicks)

`L2ArbBot.exe` is a single file that **installs the app and everything it needs on
first run**, then on every later run just launches and opens the dashboard.

### Get it

A Windows program can't be built on Linux/macOS, so the `.exe` is produced on a
Windows machine or in CI:

- **On your own Windows machine (easiest):** in the repo root, **double-click
  [`Build_L2ArbBot.bat`](../Build_L2ArbBot.bat)**. It runs the build for you (no
  PowerShell flags, no execution-policy prompts) and leaves the result at
  `launcher\dist\L2ArbBot.exe`. From a terminal that's:
  ```
  Build_L2ArbBot.bat
  ```
  which is a thin wrapper around:
  ```powershell
  powershell -ExecutionPolicy Bypass -File scripts\build_windows_exe.ps1
  # → launcher\dist\L2ArbBot.exe
  ```
- **CI:** run the **`build-windows-exe`** GitHub Actions workflow
  (`Actions → build-windows-exe → Run workflow`, or push a `v*` tag). It builds
  `L2ArbBot.exe`, smoke-tests it, and uploads it as a downloadable artifact (and
  attaches it to tagged releases).

### Run it

Just **double-click `L2ArbBot.exe`**. Or from a terminal:

```
L2ArbBot.exe             # install-if-needed, then open the dashboard (paper mode)
L2ArbBot.exe doctor      # show what's installed and your recommended next step
L2ArbBot.exe setup       # go live: paste one Arbitrum RPC URL (see §2)
L2ArbBot.exe run --live  # run on real on-chain data (after setup)
```

On the first run it unpacks itself to `%LOCALAPPDATA%\L2ArbBot`, installs any
missing tools via `winget` (Python, Node, Rust), builds the components, then
launches. **First run needs internet and a few minutes** (compiling the Rust feed
is the slow part); later runs start in seconds. If a tool is installed but not yet
found, the app refreshes its `PATH` automatically — and if a fresh terminal is
truly needed it tells you to close and reopen, rather than failing cryptically.

> Windows may show a SmartScreen warning for a new unsigned app — choose *More
> info → Run anyway*. The `.exe` bundles **clean source** and builds on your
> machine; it never ships prebuilt binaries you can't reproduce.

---

## 2. Going live — one RPC URL (`setup`)

Paper mode needs no configuration. To watch **real** on-chain data you need a
read-only **RPC endpoint** — a free one from e.g. [alchemy.com](https://alchemy.com)
or [infura.io](https://infura.io) works. Then run the guided setup:

```
L2ArbBot.exe setup        # or:  python3 -m l2arb setup   (dev checkout)
```

It asks for **one** thing — your Arbitrum HTTPS RPC URL — and writes a complete,
valid, live-ready configuration for you. Everything else (the WETH/USDC token and
pool addresses) is filled in from **real, on-chain-verified** values shipped with
the app; the startup validation gate re-proves every one on-chain, so nothing is
taken on trust. The WebSocket URL is derived from your HTTPS URL automatically for
the common providers.

Non-interactive (scriptable) forms:

```
l2arb setup --http "https://arb-mainnet.g.alchemy.com/v2/YOUR_KEY"
l2arb setup --provider alchemy --key YOUR_KEY
l2arb setup --http "https://primary/..." --backup "https://backup/..."   # rate-limit failover
```

The `--backup` URL (or a comma-separated `ws_url`/`http_url` in the config) is a
second endpoint the app **fails over to automatically** if the first is
rate-limited. After setup, go live with `l2arb run --live` — or just relaunch; the
app detects a live-ready config on its own.

> **Execution stays paper.** `setup` only wires **read-only** data sources.
> Broadcasting a real flash-loan transaction is a separate, deliberate,
> human-authorised step against audited contracts — see `CLAUDE.md` §2 and
> `contracts/docs/INTEGRATION.md`. The bot never signs or sends on its own.

Want more than Arbitrum, or fully custom pools? Edit `.l2arb/config.toml` directly
using `ingestion/config/config.example.toml` as the reference (per-chain `ws_url` +
`http_url`, and curated `pool_registry` files under
`ingestion/config/pools/<chain>.toml`). `setup` gets you a working single-chain
deployment; the config file is the full surface for everything else.

---

## 3. The launcher (cross-platform, for developers)

Requires: **Python 3.11 or 3.12**, **Node ≥ 20** (+ pnpm via Corepack), **Rust ≥
1.94** (only for live on-chain data). On Windows these are auto-installed via
`winget`; on Linux/macOS install them yourself (the launcher detects and guides).

```bash
cd launcher
python3 -m l2arb doctor      # toolchains + install state + your next step
python3 -m l2arb install     # build engine venv + dashboard + ingestion binary
python3 -m l2arb run         # paper mode → http://localhost:8787 (opens browser)
python3 -m l2arb setup       # live setup (one RPC URL)
python3 -m l2arb run --live  # full stack on real data
```

- `run` — **paper/simulation dashboard**, zero config. Real detection wiring,
  simulated fills. If nothing is built yet, `run` builds it first.
- `run --live` — full stack on real data (needs `setup`, or a hand-filled config).
- `--no-browser`, `--port N`, `--paper` are available. `install --paper-only`
  builds just the dashboard (skips the engine + Rust build).

State (venv, generated `config.toml`, pool registries, logs, install marker) lives
under `.l2arb/` in the workspace. Set `L2ARB_HOME` to relocate it.

---

## 4. Docker

```bash
cp ingestion/config/config.example.toml ingestion/config.toml   # then edit (see §2)
docker compose up --build
# dashboard UI → http://localhost:8080   ·   API → http://localhost:8787
```

Four services on one network (`engine`, `ingestion`, `backend`, `frontend`),
pre-wired for the external feed. Until `ingestion/config.toml` has real endpoints,
the engine and ingestion run but emit no opportunities (we never fabricate on-chain
data); the dashboard is still reachable.

---

## Per-component build/test

Each component is independently buildable:

```bash
cd engine     && make check                 # Python: lint + types + tests + coverage
cd ingestion  && cargo test --workspace     # Rust: unit + property + on-chain-equality
cd dashboard  && pnpm install && pnpm verify# Node: typecheck + tests + build
cd contracts  && bash scripts/verify.sh     # Solidity: fmt + build + tests (needs forge)
python3 -m unittest discover -s launcher/tests   # launcher unit tests
```

## Troubleshooting

- **`doctor` shows a toolchain missing** — install it (the report says which
  version) and re-run. On Windows the `.exe` attempts `winget` automatically and
  refreshes `PATH`; if it still can't find a just-installed tool, close the window
  and run again.
- **Dashboard opens but shows no opportunities** — in paper mode a stream appears
  within seconds; in live mode confirm you ran `setup` (or that `config.toml` has
  real endpoints) and check `.l2arb/logs/ingestion.log` and `engine.log`.
- **"config validation failed" during `setup`** — the URL you pasted wasn't a
  valid endpoint; re-run `setup` with the full URL your provider gave you.
- **Port already in use** — pass `--port` to the launcher, or stop whatever holds
  `8787`/`8080`/`9001`.
