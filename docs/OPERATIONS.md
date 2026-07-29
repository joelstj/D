# Operations Guide — install, configure, run, and every credential explained

This is the complete reference for getting the L2 Arbitrage Flash-Loan Bot
running and keeping it running: every install path, every API key and
credential the system can use (which ones you actually need, which are
optional, and exactly where to get and paste each one), and day-to-day
operation once it's up.

- For the fastest path (Windows `.exe` double-click, or a one-command
  launcher paper run), see **[`docs/INSTALL.md`](INSTALL.md)** — come back
  here for the full credentials reference and ongoing operation.
- For the data flow and integration seams, see
  **[`docs/ARCHITECTURE.md`](ARCHITECTURE.md)**.
- For deploying your *own* copy of the smart contracts, see
  **[`contracts/docs/DEPLOYMENT.md`](../contracts/docs/DEPLOYMENT.md)** — this
  doc only covers the credentials that step needs, not the deploy mechanics.

---

## 1. What you actually need, by use case

The bot is useful with **zero external credentials**. Everything beyond that
is additive — pick the row that matches what you're trying to do:

| You want to... | Credentials required | Notes |
| --- | --- | --- |
| Try the dashboard, see the UI, understand the mechanics | **None** | Default `EXECUTION_MODE=paper`, `DATA_SOURCE=simulated`. Zero config. |
| Watch **real** on-chain opportunities on one chain | **1 RPC endpoint** (e.g. Arbitrum) | `l2arb setup` asks for exactly this. |
| Watch real opportunities across all 5 supported L2s | **Up to 5 RPC endpoints** (one per chain) | Same provider account usually covers all of them. |
| Harden the engine's data-integrity cross-check | + **1 Blockscout API key** (optional) | Free; only raises a public rate limit. |
| Connect a wallet other than MetaMask's browser extension | + **1 WalletConnect (Reown) project ID** (optional) | Cosmetic UI feature only. |
| Deploy your **own** instance of the flash-loan contract | + **1 funded deployer private key** | Human-gated, one-time, never used by the running bot. |
| Verify your deployed contract's source on a block explorer | + **1 Etherscan-family API key** (optional) | Only used by `hardhat verify` / `forge --verify`. |
| Execute a **live** (real) flash-loan trade | **Nothing beyond the above** — and a conscious decision | The bot never signs or broadcasts on its own; see [§6](#6-going-from-detection-to-a-live-trade-what-actually-changes). |

Nothing in this table is a trading-venue API key, a CEX key, or a price-feed
key (CoinGecko/CMC/etc.) — **the bot uses none of those.** Every price comes
from on-chain state read directly over RPC (see `CLAUDE.md` §2, invariant 1).

---

## 2. Credentials reference — how and where to get each one

### 2.1 RPC / node provider — the one credential most people need

**What it's for.** Every price the engine ever sees comes from reading live
L2 chain state — pool reserves, swap events, block headers — over JSON-RPC
(HTTP) and, for the low-latency hot path, a WebSocket subscription (`eth_subscribe`).
Public, free RPC endpoints exist for every supported chain (they're what the
Hardhat/Foundry configs fall back to for compiling and testing), but they're
rate-limited and usually HTTP-only — fine for trying things out, not for
sustained live detection. A dedicated provider gives you a private HTTPS +
WSS pair with a real rate limit.

**Where to get one** (any of these work; pick one):

| Provider | Sign up | Notes |
| --- | --- | --- |
| Alchemy | <https://alchemy.com> | Free tier; HTTPS + WSS per app. Broad L2 coverage. |
| Infura | <https://infura.io> | Free tier; HTTPS + WSS per project. |
| QuickNode | <https://quicknode.com> | Free trial / paid tiers; per-chain endpoints. |
| A chain's own public RPC | see <https://chainlist.org> | No signup, but shared/rate-limited — fine for `doctor`/compiling, not for sustained live ingestion. |

**Steps (the pattern is the same across providers):**
1. Create a free account.
2. Create an "app" / "endpoint" and pick the network — **Arbitrum One**,
   **Base**, **Optimism**, **Polygon** are broadly supported everywhere;
   **Unichain** and **Ink** are newer, so confirm your provider actually lists
   them before relying on it (check the provider's own network list, or
   chainlist.org, rather than assuming — this codebase itself ships `Unichain`/`Ink`
   provider addresses as `null`/"verify" for the same reason; see
   `contracts/config/addresses.js`).
3. Copy the **HTTPS** URL (this *is* your API key — it's embedded in the URL,
   e.g. `https://arb-mainnet.g.alchemy.com/v2/<KEY>`) and the **WSS** URL
   (`wss://arb-mainnet.g.alchemy.com/v2/<KEY>`).
4. One RPC key per chain is enough — you don't need separate keys for HTTP
   vs WS, they're the same credential in two URL schemes.

**Where it goes** — depends on which component is consuming it:

| Component | File | Variable |
| --- | --- | --- |
| Ingestion (the real, low-latency 5-chain feed) | `ingestion/config.toml` (copy from `ingestion/config/config.example.toml`) | `[[chains]] ws_url` / `http_url`, per chain |
| Engine standalone mode (rare — normally fed by ingestion, not its own RPC) | repo-root `.env` | `L2ARB__CHAINS__<CHAIN>__HTTP` / `__WSS` |
| Dashboard's own optional live provider (WIP; not needed for the normal `DATA_SOURCE=external` path) | repo-root `.env` | `RPC_URL_ARBITRUM`, `RPC_URL_BASE`, `RPC_URL_OPTIMISM`, `RPC_URL_POLYGON`, `RPC_URL_UNICHAIN`, `RPC_URL_INK` |
| Contracts deploy / fork-test tooling (human-gated, one-time) | repo-root `.env` (or `contracts/.env`) | `ARBITRUM_RPC_URL`, `BASE_RPC_URL`, `OPTIMISM_RPC_URL`, `POLYGON_RPC_URL`, `UNICHAIN_RPC_URL`, `INK_RPC_URL`, `FORK_RPC_URL` |
| `l2arb setup` (the guided flow) | writes `.l2arb/config.toml` for you | just paste the HTTPS URL when it asks |

**Failover.** Both `ingestion/config.toml` and the launcher's `setup --backup`
flag accept a **comma-separated list** of endpoints (`"https://primary, https://backup"`).
On a rate limit (HTTP 429) or transport error, reads hand off to the next one
automatically — worth doing if you're on a free tier.

---

### 2.2 Blockscout API key — optional data-integrity hardening

**What it's for.** The engine cross-checks RPC-derived pool state against
[Blockscout](https://blockscout.com), an independent block explorer, as a
second source of truth before trusting a pool (see `docs/DATA_INTEGRITY.md`
in the engine). Blockscout's public API works with **no key at all** — you
only need one to raise the rate limit. It is **read-only**: never a signing
or write credential, and the engine never uses it for anything but a GET
request to the fixed, built-in per-chain Blockscout endpoint.

Currently wired for **Arbitrum, Base, and Optimism** (`BLOCKSCOUT_REST_BASES`
in `engine/src/l2arb/config.py`); Unichain and Ink aren't in that map yet.

**Where to get one.** <https://blockscout.com> → sign in → API Keys → generate.

**Where it goes.** Repo-root `.env`:

```
L2ARB__BLOCKSCOUT__API_KEY=your-key-here
```

That's the *only* thing you configure — the per-chain REST endpoints are
built into the engine, so there's no URL to look up or get wrong.

---

### 2.3 WalletConnect (Reown) project ID — optional, cosmetic

**What it's for.** The dashboard's MetaMask connection works out of the box
via the browser's injected `window.ethereum` — **no credential needed** for
that. A WalletConnect project ID only matters if you want the dashboard to
also offer WalletConnect-based wallets (mobile wallets, hardware wallets via
WalletConnect, etc.) beyond a browser extension.

**Where to get one.** WalletConnect's cloud dashboard was rebranded to
**Reown** — <https://cloud.reown.com> (old `cloud.walletconnect.com` links
redirect there). Sign in → Create Project → copy the **Project ID**.

**Where it goes.** Repo-root `.env` (read by the Vite frontend build):

```
VITE_WALLETCONNECT_PROJECT_ID=your-project-id
```

---

### 2.4 Deployer private key — only if you deploy your own contract

**This is the one real secret in the whole system**, and it is used **only**
by the human-gated, one-time contract deployment step — never by the running
detection stack (engine/ingestion/dashboard hold no keys and sign nothing;
see `CLAUDE.md` §2, invariant 2–3).

You do **not** need this at all if you're only running detection/paper mode,
or if you point the dashboard's execution config at an already-deployed
`FlashLoanArbitrage` contract someone else deployed and verified.

**Where it comes from.** This is *your* wallet's private key, not something
you sign up for. Practical guidance:
- Use a **fresh wallet dedicated to deployment only** — never your main
  holdings wallet.
- Fund it with just enough native gas token for the deploy + role-setup
  transactions on your target chain.
- After deploying, follow the "post-deploy hardening" steps in
  `contracts/docs/DEPLOYMENT.md` — grant `EXECUTOR_ROLE` to a separate hot
  bot key, move `GUARDIAN_ROLE`/`DEFAULT_ADMIN_ROLE` to a multisig, and
  consider having the deployer key renounce its own admin role once that's
  done.
- For anything beyond a testnet, prefer a hardware wallet or a multisig
  deploy flow over a plaintext key in `.env` where practical.

**Where it goes.** Never commit it. Either the repo-root `.env` (in its
clearly-fenced *Contracts* section) or, to keep it out of the shared file
entirely, a local `contracts/.env` (which overrides the master and is also
git-ignored):

```
PRIVATE_KEY=0xyour-deploy-only-key
```

---

### 2.5 Etherscan-family API key — optional, only for source verification

**What it's for.** Purely cosmetic/trust-building: it lets `npx hardhat
verify` or `forge script --verify` publish your deployed contract's source
code on the block explorer, so anyone can read it instead of just bytecode.
It has zero effect on how the contract runs.

**Where to get one.** <https://etherscan.io> → sign up → *My Profile → API
Keys → Add*. Etherscan's current (V2) API key works across its whole
supported-chain family — the same key covers the Arbitrum, Base, Optimism,
and Polygon explorers, which is why `hardhat.config.js` only asks for one
`ETHERSCAN_API_KEY`.

**Where it goes.** Repo-root `.env`:

```
ETHERSCAN_API_KEY=your-key-here
```

---

### 2.6 What you deliberately do *not* need

Worth stating explicitly, since it's easy to assume otherwise:

- **No CEX API keys** (Binance, Coinbase, etc.) — this bot never touches a
  centralized exchange.
- **No price-feed API key** (CoinGecko, CoinMarketCap, Chainlink hosted
  feeds, ...) — every figure comes directly from on-chain reserves/logs; the
  dashboard's mapper is explicit that it never fabricates a USD price for a
  non-stablecoin numeraire (`docs/ARCHITECTURE.md`, Seam B honesty note).
- **No Redis/Postgres cloud service** — the engine's dependency set includes
  `redis`/`sqlalchemy`/`asyncpg` for an in-progress persistence/analytics
  layer (`make services-up` spins up local instances for that test tier),
  but the shipped pipeline (the launcher's `run`/`run --live`, and
  `docker-compose.yml`'s four services) does not provision or require them.
- **No signing key for the engine, ingestion, or dashboard** — only the
  one-time contract deployment step in §2.4 ever touches a private key.

---

## 3. Where every credential lives — the master `.env`

Every credential above (except the deployer private key, if you choose to
isolate it) lives in **one file**: `.env` at the repo root, created from the
tracked template:

```bash
cp .env.example .env
```

Precedence (highest wins): real environment variables → a component-local
`.env` (e.g. `contracts/.env`, `engine/.env` — optional, for overriding just
one piece) → the repo-root master `.env` → built-in defaults. Every value has
a safe default, so an unfilled `.env` still runs — in paper/simulated mode.

The **one exception** is the Rust ingestion layer: it's configured by
`ingestion/config.toml` (TOML, not environment variables) — copy
`ingestion/config/config.example.toml` and fill in your RPC endpoints and pool
registries there. The `l2arb setup` command writes this file for you from a
single pasted RPC URL, using real, on-chain-verified token/pool addresses
already shipped with the app.

`.env` and `config.toml` are both git-ignored — only their `.example`
templates are tracked. **Never commit a real key.**

---

## 4. Installing

Full step-by-step detail (Windows `.exe`, the cross-platform launcher, and
Docker) lives in **[`docs/INSTALL.md`](INSTALL.md)** — this section is the
condensed version so this doc is self-contained.

```bash
# Cross-platform, from a dev checkout:
cd launcher
python3 -m l2arb doctor      # checks Python 3.11/3.12, Node ≥20, Rust ≥1.94
python3 -m l2arb install     # builds the engine venv + dashboard + ingestion binary
python3 -m l2arb run         # paper mode → http://localhost:8787, zero config
```

Going live is the same launcher, plus the one RPC credential from §2.1:

```bash
python3 -m l2arb setup       # paste one RPC URL — writes a complete, validated config
python3 -m l2arb run --live  # engine + ingestion + dashboard on real on-chain data
```

On Windows, `L2ArbBot.exe` wraps the same launcher into a single
double-click-to-install file (see `docs/INSTALL.md` §1 for how to obtain or
build it). Docker is the container-native alternative (`docker compose up
--build`, after filling `ingestion/config.toml`).

Per-component toolchains, if you're working on one piece directly:

```bash
cd engine     && make setup && make check           # Python 3.11–3.12, uv
cd ingestion  && cargo build --release                # Rust 1.94, pinned via rust-toolchain.toml
cd dashboard  && pnpm install && pnpm verify           # Node ≥20 + pnpm (via Corepack)
cd contracts  && bash scripts/verify.sh                # Solidity 0.8.20; needs Foundry (scripts/bootstrap.sh installs it)
```

---

## 5. Operating it day to day

**Starting up.** `python3 -m l2arb run` (paper) or `run --live` (real data).
The launcher health-gates each service on the way up (won't declare "ready"
until `/health` actually answers) and opens the dashboard in your browser.

**Watching it work.**
- The dashboard itself, at `http://localhost:8787` by default (`PORT` in
  `.env`), shows live opportunities, the *Pipeline Latency* HUD
  (`GET /api/latency`), and — read-only, never a broadcast —
  `GET /api/health/execution`.
- The launcher's terminal HUD shows per-service process + `/health` status
  and self-heals a crashed or wedged process (bounded restarts with
  backoff) — infrastructure only; it never touches the execution path.
- Ingestion exposes `:9090/health` and `:9100/metrics` (Prometheus) directly
  if you want to scrape it yourself.
- Logs live under `.l2arb/logs/` in a dev checkout (`ingestion.log`,
  `engine.log`); set `L2ARB_HOME` to relocate the whole state directory.

**Switching paper ↔ live.** Paper is the default and always safe — it never
broadcasts. Going live only changes *what data you watch* (`DATA_SOURCE=external`
fed by real RPC via ingestion) — it does **not** by itself execute trades.
Actual execution is the separate, explicit, human-authorised step described
next.

**Stopping.** Ctrl-C in the launcher's terminal stops the whole stack
cleanly (each supervised process is signaled and the health monitor exits).
For Docker, `docker compose down`.

**Updating.** Pull the latest code, then re-run `python3 -m l2arb install`
(or the per-component build command) to rebuild anything that changed;
`doctor` will tell you if a toolchain version has drifted out of the range
it expects.

---

## 6. Going from detection to a live trade — what actually changes

This bot is intentionally **safe by default at every layer**, and no
credential in §2 changes that:

1. Detection (engine) never holds a key and never signs anything.
2. The dashboard ships `EXECUTION_MODE=paper` — `POST /api/execute/:id` runs
   a simulated fill, not a transaction.
3. Real execution targets an audited `FlashLoanArbitrage` contract that is
   itself gated by an on-chain `EXECUTOR_ROLE` and reverts atomically unless
   realized profit clears your `minProfit` — an unprofitable attempt costs
   only gas, never capital.
4. The documented safe integration pattern is **simulate via
   `eth_call`/`staticCall` first** (a revert just means "not profitable right
   now"), **then hand an unsigned transaction to a human-authorised signer**.
   The loop itself never broadcasts.

Turning any of this into a live-signing bot is a deliberate integration you
build on top (your own signer, your own risk limits) — it is explicitly out
of scope for what ships here (see `CLAUDE.md` §2 and §7).

---

## 7. Troubleshooting credentials specifically

- **`l2arb setup` says "config validation failed"** — the pasted URL wasn't
  a reachable HTTPS endpoint; re-paste the exact URL your provider gave you
  (including the trailing key path).
- **Dashboard is live but shows no opportunities** — confirm `ingestion/config.toml`
  (or `.l2arb/config.toml`) has *real* endpoints, not the `YOUR_..._WS`
  placeholders from the example file; check `.l2arb/logs/ingestion.log`.
- **Blockscout cross-checks failing / rate-limited** — this is expected
  without a key (public tier is rate-limited, not broken); add
  `L2ARB__BLOCKSCOUT__API_KEY` from §2.2, or reduce request volume.
- **`hardhat verify` / `forge --verify` fails with an API-key error** — you
  need `ETHERSCAN_API_KEY` (§2.5); this only affects source verification, not
  the deployment itself.
- **A chain shows `null` with a `// VERIFY:` comment in
  `contracts/config/addresses.js`** — that address is genuinely unconfirmed
  in this codebase (Unichain/Ink providers, mainly); verify it yourself
  against the protocol's official docs before using it. Never treat an
  unverified address as real (`CLAUDE.md` §2, invariant 7).
- **Accidentally committed a key** — rotate/revoke it at the provider
  immediately (git history keeps it even after a later commit removes it);
  then re-`cp .env.example .env` and re-fill it.
