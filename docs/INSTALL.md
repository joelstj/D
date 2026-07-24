# Install & run

Three ways to run the bot, from easiest to most manual.

---

## 1. The Windows `.exe` (self-bootstrapping)

`L2ArbBot.exe` is a single file that **installs the app and all dependencies on
first run**, then on every later run just launches the stack and opens the
dashboard.

### Get it

A Windows PE cannot be produced from Linux/macOS, so the `.exe` is built on a
Windows runner:

- **CI (recommended):** run the **`build-windows-exe`** GitHub Actions workflow
  (`Actions → build-windows-exe → Run workflow`, or push a `v*` tag). It builds
  `L2ArbBot.exe`, smoke-tests it, and uploads it as an artifact (and attaches it
  to tagged releases).
- **Locally on Windows:**
  ```powershell
  powershell -ExecutionPolicy Bypass -File scripts\build_windows_exe.ps1
  # → launcher\dist\L2ArbBot.exe
  ```

### Run it

Double-click `L2ArbBot.exe`, or from a terminal:

```
L2ArbBot.exe            # auto: install-if-needed, then run + open the dashboard
L2ArbBot.exe doctor     # show toolchains + install state
L2ArbBot.exe run --live # full on-chain stack (after filling config — see §4)
```

On first run it unpacks the bundled component sources to
`%LOCALAPPDATA%\L2ArbBot`, installs missing toolchains via `winget`
(Python 3.12, Node LTS, Rust), builds the components, then launches. First-run
install needs internet and takes several minutes (the Rust build is the slow
part); later runs start in seconds.

> The `.exe` bundles **clean source** and builds on your machine — it never ships
> prebuilt binaries you can't reproduce. To build the exe yourself on any OS for
> testing (produces a native binary, not a Windows PE):
> `pip install pyinstaller && python scripts/build_exe.py`.

---

## 2. The launcher (cross-platform, for developers)

Requires: **Python 3.11 or 3.12**, **Node ≥ 20** (+ pnpm via Corepack), **Rust ≥
1.94** (only for live on-chain data).

```bash
cd launcher
python3 -m l2arb doctor      # verify toolchains + state
python3 -m l2arb install     # build engine venv + dashboard + ingestion binary
python3 -m l2arb run         # paper mode → http://localhost:8787 (opens browser)
```

- `python3 -m l2arb run` — **paper/simulation dashboard**, zero config. Real
  detection wiring, simulated fills.
- `python3 -m l2arb run --live` — full stack on real data (needs §4).
- `--no-browser`, `--port N`, `--paper` are available. `install --paper-only`
  builds just the dashboard (skips the engine + Rust build).

State (venv, generated `config.toml`, logs, install marker) lives under
`.l2arb/` in the workspace. Set `L2ARB_HOME` to relocate it.

---

## 3. Docker

```bash
cp ingestion/config/config.example.toml ingestion/config.toml   # then edit (see §4)
docker compose up --build
# dashboard UI → http://localhost:8080   ·   API → http://localhost:8787
```

Four services on one network (`engine`, `ingestion`, `backend`, `frontend`),
pre-wired for the external feed. Until `ingestion/config.toml` has real
endpoints, the engine and ingestion run but emit no opportunities (we never
fabricate on-chain data); the dashboard is still reachable.

---

## 4. Going live (real on-chain data)

Live mode needs **real, read-only** RPC endpoints and curated pool registries —
the bot never invents these (on-chain data integrity).

1. Generate/locate the ingestion config:
   - launcher: `.l2arb/config.toml` (created on first `install`/`run`);
   - docker: `ingestion/config.toml`.
2. Fill it from `ingestion/config/config.example.toml`:
   - per chain: `ws_url` (low-latency, hot path) + `http_url` (archive, seeding);
   - curated `pool_registry` files under `ingestion/config/pools/<chain>.toml`
     (real, on-chain-verified pools);
   - `[engine] http_url` = `http://127.0.0.1:8080` (launcher) or
     `http://engine:8080` (docker);
   - `[output] sink = "ws"`, `ws_bind = "0.0.0.0:9001"`.
3. Point the dashboard at the feed: `DATA_SOURCE=external`,
   `INGEST_FEED_URL=ws://127.0.0.1:9001` (the launcher sets these for you in
   `--live` mode).

Execution stays in **paper** mode regardless. Enabling real execution is a
separate, deliberate, human-authorised step against audited contracts — see
`CLAUDE.md` §2 and `contracts/docs/INTEGRATION.md`.

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
  version) and re-run. On Windows the `.exe` attempts `winget` automatically.
- **Dashboard opens but shows no opportunities** — in paper mode a stream appears
  within seconds; in live mode confirm `config.toml` has real endpoints and check
  `.l2arb/logs/ingestion.log` and `engine.log`.
- **Port already in use** — pass `--port` to the launcher, or stop whatever holds
  `8787`/`8080`/`8080`/`9001`.
