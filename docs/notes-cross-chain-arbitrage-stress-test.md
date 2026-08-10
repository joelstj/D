# Research notes — cross-chain arbitrage stress test (2026-08-10)

Branch `claude/cross-chain-arbitrage-stress-test-psxjbh`. Scratch/anchor doc per the TDD loop — if
something built later contradicts a finding here, stop and reconcile rather than pushing forward.

## Task

"Run a comprehensive stress test and report any gaps or problems we have that need to be filled and
try to execute one cross chain arbitrage flash loan smart contract successfully."

This is the 7th full audit pass on this repo (root `CLAUDE.md` §8–§14 record the prior six) and the
2nd focused specifically on cross-chain (after §12). Method: re-verify environment/deployment state
directly rather than trusting prior notes; re-confirm every prior fix still holds by reading current
HEAD; five parallel investigations (one per component, contracts handled directly by this session)
each briefed with the prior audits' exact findings so they hunt for *new* gaps instead of rediscovering
old ones; every baseline and post-fix gate re-run independently by the orchestrating session itself,
not just trusted from a sub-agent's report.

## Environment (re-verified fresh, not assumed from notes)

| Fact | Value | How verified |
|------|-------|---------------|
| Base balance | 0.003452513767438216 ETH | `eth_getBalance`, live |
| Base deployment | `0x17fB2Da9D6b6f95962Ad21f39aAE43f40Caaf602` — **still live** | `eth_getCode` (13,660 bytes), `aavePremiumBps()`→5, `paused()`→false |
| Arbitrum balance | 0.00000234644432415 ETH (dust) | `eth_getBalance`, live |
| Optimism balance | 0 | `eth_getBalance`, live |
| Polygon balance | 0.0911430921244852 POL | `eth_getBalance`, live (via `polygon.gateway.tenderly.co` — the operator-configured `POLYGON_RPC` env var is a malformed, comma-joined value; noted below, not fixed — it's operator env, not repo config) |
| `contracts/deployments/` | does not exist in this fresh container | `ls` — expected: git-ignored by design (root `CLAUDE.md` §14), a fresh container never inherits a prior session's local deploy record even though the real on-chain contract persists |
| Foundry / `forge` | **still BLOCKED locally, but closer than ever** — see below | `bash scripts/bootstrap.sh`, then `foundryup --install 1.5.1` directly |

### Foundry: new, more precise information about the blocker

Every prior session recorded `forge` as simply "not installed." This session went one step further:
`curl -fsSL https://foundry.paradigm.xyz | bash` succeeded (outbound network reaches it), and
`foundryup --install 1.5.1` **downloaded all five binaries at 100%** — the furthest any session has
gotten. It then failed at the **attestation/SHA verification** step ("no expected hash for forge,
cast, anvil, chisel"). `foundryup -f/--force` exists specifically to skip that check, labelled
"INSECURE" by the tool itself. **Declined deliberately**: this container carries a real
`EXECUTOR_PRIVATE_KEY`, and running an unverified downloaded binary on a key-holding machine is
exactly the kind of shortcut worth refusing even when the download itself looks legitimate — the
verification step existing at all means a mismatch is a signal, not noise, and the cost of being
wrong (a compromised `forge` on a machine with a real key) is not worth the benefit (running the
Solidity suite locally instead of trusting CI, which already runs it — root `CLAUDE.md` §13). Recorded
as BLOCKED, not faked, with this more precise detail for whichever session picks it up next — possibly
a proxy/attestation-fetch interaction specific to this sandbox's outbound proxy, not a real
supply-chain issue, but not confirmed either way and not worth guessing on a key-holding container.

### `POLYGON_RPC` env var is malformed (operator environment, not repo code)

`process.env.POLYGON_RPC` is a comma-joined value missing a protocol prefix on its first segment,
causing `ethers`' `JsonRpcProvider` to retry indefinitely rather than fail cleanly. Worked around by
using public fallback RPCs (`polygon.gateway.tenderly.co`, already the exact endpoint this repo's own
`CrossChainDualFork.test.js` docstring recommends) for every live check and script run this session.
This is operator-environment configuration, outside repo scope — flagged here so a future session
doesn't waste time rediscovering it, not fixed (nothing in this repo reads `POLYGON_RPC`; only
`POLYGON_RPC_URL` per `hardhat.config.js`).

## Baseline (independently re-run by the orchestrating session, before AND after every fix — not
just trusted from an audit sub-agent's report)

| Gate | Before | After |
|------|--------|-------|
| contracts Hardhat offline | 66 | **73** (+7, all new cross-chain-script guard tests) |
| contracts cross-chain dual-fork (live) | — | **1 passing**, re-run live this session |
| engine `make check` | 460 | **469** (+9), 99.87% coverage maintained |
| ingestion fmt+clippy+test | 204 | **216** (+12), fmt clean, clippy clean (`-D warnings`) |
| dashboard `pnpm verify` | 126+45=171 | **131+62=193** (+22), typecheck clean, both builds clean |
| launcher unittest | 80 | **90** (+10) |

**884 → 954 tests total across the five components**, every gate re-run green by this session
directly (not just trusted from a sub-agent), matching the discipline established in §9–§14.

## Findings

Severity is about "how badly this blocks or misrepresents a real, successful cross-chain trade" and
"how badly this could misprice/misreport an opportunity," matching prior audits' framing. FIX =
addressed this session with a regression test, independently re-verified by the orchestrating session
(diff read directly + gate re-run). RECORD = confirmed real, deliberately not fixed, reasoning stated.

### Engine (`engine/src/l2arb/`)

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| EN1 | HIGH (data-integrity) | `BridgeQuote.fee_bps`/`fixed_fee`/`settle_seconds` had zero validation, unlike every sibling domain object (`PoolState.fee_pips`, `Blockstamp`, `Token`). A negative `fee_bps` makes `net_after()` return *more* than the bridged amount — manufactured, `verified:true`-reported profit from a config typo or malformed request, reachable since the Rust ingestion side's `fee_bps: f64` also has no lower-bound check. | **FIX** — `__post_init__` validation raising `DataError`, mirroring the established pattern; `BridgeSpec` in `api/schema.py` also gets `Field(ge=0)` so a malformed HTTP request 422s at the boundary. 3 rejection tests + 1 Hypothesis property test (`net_after(amount) <= amount` for any constructible quote). |
| EN2 | HIGH | Cross-chain dedup key (`ranking.py::_dedup_key`) built `frozenset(opp.pool_addresses)` from bare address strings with no chain tag — the same shape as the already-fixed ingestion bug (§12/I2), just never checked on the engine side. A genuine cross-chain opportunity sharing one same-address pool with an unrelated same-chain opportunity (real risk: several shipped chains are OP-Stack siblings with identical predeploy addresses) collides and one is silently dropped. | **FIX** — key now built from `(leg.token_in.chain_id, leg.pool_address)` pairs. |
| EN3 | MEDIUM | `_detect_cross_chain` only ever passed the *buy*-chain's `min_profit_bps` to `cross_chain_two_hop`, so an operator's stricter per-chain threshold configured for the *destination* chain was silently unenforced. | **FIX** — now uses `max(buy_ctx.min_profit_bps, sell_ctx.min_profit_bps)`, strictly more conservative than either side alone. |
| EN4 | LOW (docs) | Root `.env.example` was missing `L2ARB__CROSS_CHAIN_PRICE_DRIFT_BPS_PER_MINUTE` — the one `Settings` field added by the prior cross-chain audit (§12/E1) that was never added to the example file, despite `config.py`'s own docstring claiming full documentation coverage. | **FIX** (doc-only). |

Re-verified present and correct at HEAD, not re-litigated: the §12 E1/E4/E5 fixes (price-drift
haircut, settle-time-scaled risk penalty, numeraire fungibility check) — all read directly in
`cross_chain.py`/`profit.py`, all still backed by passing tests. One item investigated and found to
be pre-existing, out of this session's scope: `AssetSpec.representations`'s loosely-typed
`list[dict]` raises an unhandled `KeyError` on a malformed entry instead of a clean validation error
— not a cross-chain-audit-introduced regression, a proper fix is a larger schema change; recorded
only.

### Ingestion (`ingestion/crates/`)

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| IN1 | **CRITICAL** (core property fixed; health-endpoint visibility left as a recorded follow-up) | No liveness/staleness watchdog on a chain's `heads` WS subscription — the single most significant open finding carried across *two* prior audit cycles (§11 recorded it CRITICAL and live-reproduced; §12 didn't re-attempt it, citing the need to restructure supervisor/health startup order). An upstream node's WS can go quiet **without ever erroring**, distinct from a clean disconnect the loop already handles — nothing in the old `select!` had a branch that could ever notice, so `mark_all_unverified()`/reconnect never fired and the mirror stayed `verified:true` forever, unbounded — a direct violation of the `verified` honesty invariant (root `CLAUDE.md` §2 item 7). Especially costly for cross-chain trades specifically: a cross-chain opportunity is exposed to two chains' staleness risk at once, over the multi-minute non-atomic settle window EN's price-drift gate exists to price. | **FIX (partial by design)** — new `stale_after()` pure threshold function (floor 30s under a 20x-block-time multiplier, so fast chains aren't false-positived by ordinary jitter) plus a `tokio::time::interval` watchdog branch in the `select!` loop; on trip, returns `Err` to reuse the exact same, already-tested supervisor recovery path the "stream ended" branches use. Deliberately gated on `heads` alone (block cadence is a reliable liveness signal regardless of trading activity) not `logs` (event-driven, legitimately quiet on a healthy-but-idle pool). The **core safety property is closed**: `verified:true` is now bounded to roughly 30-40s of true staleness instead of unbounded. Deliberately **not** covered: `/health` still unconditionally returns `{"status":"ok"}` and the `CHAINS_LIVE` gauge is still set once at spawn — neither reflects a stalled-then-reconnecting chain in real time. Fixing that needs restructuring `pipeline.rs::run()`'s startup order (the health router is built before per-chain state exists), judged genuinely out of proportion for this session and recorded rather than rushed on a safety-relevant path (matching the discipline §11 itself used). 5 new unit tests covering floor/multiplier/clamping/boundary behavior. |
| IN2 | HIGH | HTTP endpoint failover never actually triggered for a genuinely dead endpoint — its primary documented use case, empirically proven in §11's audit and recorded but not fixed. Every RPC read method collapsed any `alloy` transport error straight to `RpcError::Call`, so `is_failover_error` (which only recognises `Call` as failover-worthy via a rate-limit *message* substring) never saw a raw connection failure as anything but an ordinary call error. | **FIX** — new `classify()` helper uses `alloy` 1.8.3's own `is_transport_error()` to correctly route connection/DNS/TLS/timeout/non-2xx failures to `RpcError::Transport` (failover-worthy) while genuine JSON-RPC application errors (reverts, decode errors — every endpoint would agree on these) stay `RpcError::Call` exactly as before. Applied to all 9 read call sites. Regression tests construct both error shapes directly and assert the classification, **plus one empirical live test** that connects `AlloyProvider` to a closed local port (`127.0.0.1:1`) and proves the real production path now returns `Transport`/failover-worthy — fails against the pre-fix code, passes now. |
| IN3 | MEDIUM (new this session) | Re-checking the seed path for all three DEX kinds (not just V4, which a prior session's framing might suggest): `registry/src/gate.rs` plus `ingest/src/v2.rs`/`v3.rs` and `v4/src/stateview.rs` gate pool entry on code-existence length only — none reject a decoded-degenerate pool (uninitialized V4 `sqrtPriceX96==0`, an unfunded fresh V2 pair, a never-initialized V3 pool), so such a pool seeds as `verified:true` with zero real price/liquidity. Broader than previously scoped. | **RECORDED** — a correct fix has to decide what happens to an excluded pool for the rest of its live session (whether the mirror's live-event handlers can adopt a pool that was never seeded), which needs verifying the mirror's insert-vs-update-only semantics first — a real design question, not a same-session patch. |
| IN4 | — (proof, no defect) | Investigated whether the WS envelope schema (`Envelope{kind:"opportunities", payload: DetectResponse}`) could silently drop cross-chain-specific fields (`is_cross_chain`, `settle_seconds`, multi-entry `chain_ids`) between the engine response and the wire JSON the dashboard's `ExternalProvider` consumes — every existing fixture only ever exercised the same-chain-shaped default values. | **No defect** — serialization has no field allowlist/projection, so there was no code path that *could* drop a field, but nothing had proven it. New regression test constructs a genuine cross-chain payload, asserts every field lands correctly in the wire JSON, and round-trips it back into a typed `DetectResponse`. |
| IN5 | — (proof, no defect) | Re-verified the §12/I2 per-leg, chain-scoped pool-verification fix generalizes to a *genuine* two-chain, two-leg cross-chain opportunity, not just the two single-chain opportunities colliding on address that the original regression test used. | **No defect** — new test proves a real cross-chain opportunity survives when both legs are independently verified on their own chain, and is correctly dropped whole when only one leg is verified (checked against a same-address pool verified on an unrelated *third* chain, to prove the check is truly per-leg). |

Re-verified present and correct at HEAD: the §11 full-snapshot-not-incremental-delta fix, the §12/I1
(`--check-config` honest post-filter counts + warn-on-empty), I2 (chain-scoped `verified_pools`), I3
(bridge-entry-required-to-keep-asset), I4 (enabled-chain cross-referencing) fixes; also confirmed no
live bridge-fee/latency observation exists anywhere in ingestion (correctly config-sourced only,
matching contracts' C1 — ingestion doesn't overclaim liveness it doesn't have), and no cross-chain-
specific starvation path in `pipeline.rs::aggregator_loop`. Of the 14 findings §11 recorded-not-fixed,
this session's IN1/IN2 close the top two (the WS watchdog's core property, and HTTP failover); IN3 is
a newly-scoped-broader version of an already-recorded shape. The remainder (L1 data-fee sizing, the
reseed/generation-bump race — re-confirmed still defused by §11's fix #1, `IncrementalTracker`
cache-key collision — re-confirmed still unreachable in production) were re-verified unchanged, still
correctly deferred, no new information changes their disposition.

### Contracts (`contracts/`) — handled directly by the orchestrating session, not delegated

Re-verified present and correct at HEAD by reading the actual `CrossChainArbitrageExecutor.sol` and
`FlashLoanArbitrage.sol` source (not the changelog): `allowedBridgeAdapters` (deny-by-default,
`GUARDIAN_ROLE`-gated), `siblingExecutor` registry, route contiguity, the `GENERIC`-router allowlist,
profit-receiver-to-event-log consistency, two-fee optimal sizing. No regressions found; nothing fixed
here because nothing was found broken — this session's contracts work was entirely about **proving
execution**, detailed below.

1. **Re-ran the existing dual-fork proof live** (`test/fork/CrossChainDualFork.test.js`, `npm run
   test:fork:crosschain`, real `POLYGON_RPC_URL`/`ARBITRUM_RPC_URL`): still passes at HEAD. Polygon
   source leg bridged 0.0410 WETH via a real QuickSwap swap; Arbitrum destination leg settled it into
   76.797308 USDC.e via real Uniswap V3 — genuinely independent live state on both chains, one
   coherent test run, exactly as documented.
2. **Built and ran a new, stronger proof**: `scripts/live_cross_chain_fork.js` (new) — see "Cross-chain
   execution" below for the full result. Adds delivery-to-a-named-wallet (the existing test only
   checks contract-held balances) and an honestly-quantified manufactured dislocation, following the
   exact precedent `scripts/live_flash_loan_fork.js` set for the same-chain case in §13.
3. **CI wiring**: added a "Live cross-chain execution against forked live state" step to
   `.github/workflows/ci.yml`, gated on both `ARBITRUM_RPC_URL`/`POLYGON_RPC_URL` secrets, using the
   same burn-address `PROFIT_RECEIVER` convention as the sibling step — closes the same "proof exists
   but has no CI coverage" gap class §13/A2 fixed for the same-chain script.
4. **Docs**: `contracts/README.md` gained a quickstart step for the new script, corrected a stale test
   count (still said "48 tests" in the repository-layout section, predating even §13's update to 66),
   and listed both live-execution scripts in the layout table.

### Dashboard (`dashboard/`)

Explicit safety-invariant sweep run **twice** (before and after this session's own changes): zero
hits for `new Wallet(`, `privateKeyToAccount`, `createWalletClient`, `ethers.Wallet`,
`sendTransaction`, `signTransaction`, or any read of `EXECUTOR_PRIVATE_KEY`/`PRIVATE_KEY` anywhere in
`dashboard/`, outside docstrings/tests-that-assert-absence. Confirmed neither of this container's real
operator env vars is read anywhere in `dashboard/`. The only chain-write hooks anywhere in the
frontend are `useDeployContract` (pre-existing) and the new `useWriteContract` added below — both sign
exclusively via the browser's connected MetaMask, and the new hook's `functionName` is a hardcoded
literal (`"pause"`/`"unpause"`), never dynamic or user-supplied.

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| DA1 | MEDIUM→HIGH (closes §12/O2) | No tooling anywhere paused either contract short of a manual raw chain call — recorded HIGH in §12, deferred as "a reasonable near-term follow-up" pending a real deployment to make it concrete rather than speculative. `FlashLoanArbitrage` is now genuinely live on Base (§14), making this buildable within the existing safe pattern rather than decorative. | **FIX** — `ChainProbe.paused()` (read-only `staticCall`, mirrors `premiumBps()`) wired into the readiness sweep (`ReadinessResult.paused`/`crossChainPaused`, both `boolean \| null` — `null` on unreadable/reverted, never guessed `false`); Pause/Unpause buttons signed via `useWriteContract()` using the same artifact-ABI-fetch pattern as deploy. Buttons self-disable only against a *known* redundant action; stay available when state is unknown (a wrong click is a harmless `Pausable` revert, not a risk). 5 backend + 9 frontend regression tests. |
| DA2 | MEDIUM (misleading-honesty class) | A cross-chain opportunity rendered identically to a same-chain one in `OpportunitiesTable`: no destination chain or settlement-time shown, and the Execute button's tooltip said "Simulated fill (paper mode)" / "Broadcast live transaction" — both false, since a cross-chain opportunity is always recorded `"skipped"` (§12/D2) regardless of mode. | **FIX** — row now shows the resolved destination network + `~Ns settle` (or an honest "destination unresolved" when `engineMap` can't disambiguate it — never guessed), Execute tooltip gets a distinct, accurate cross-chain string. |
| DA3 | LOW (recorded §11 item, now fixed) | `NETWORK_COLORS` missing `unichain`/`ink` — two of the product's five target chains render with no distinct color. Directly undermines DA2 (a cross-chain row spanning either chain would show two indistinguishable gray dots). | **FIX** — 2 categorical colors added. |
| DA4 | trivial | `deployErrorMessage`'s copy ("Deployment cancelled in wallet") misreported a rejected pause/unpause as a cancelled deployment. | **FIX** — generalized to `walletErrorMessage`, action-agnostic copy. |

Re-verified present and correct at HEAD: D1–D4 and the §11-item-3 hub-token `qualifies()` fix (all
read directly, not from notes). Re-verified as correct, not a defect: the readiness sweep honestly
reports the cross-chain executor as absent rather than erroring/fabricating a status; no stale-cache
bug re: the real Base deployment (this container's `contracts/deployments/` and root `.env` are both
absent by design — expected sandbox isolation, not a code defect, though worth flagging operationally:
a dashboard run *in this container* would show Base as "needs deploy" until an operator re-records
that deployment here).

### Launcher (`launcher/`)

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| LA1 | HIGH | The exact TOML-injection defect class §9/§10 already fixed for `pool_registry` was never applied to `ws_url`/`http_url` in `l2arb setup` — the two fields an operator actually hand-pastes from an RPC provider dashboard (`setup.py` `arbitrum_quickstart_config`). A stray `"`/`\` in the paste corrupts the whole generated `config.toml`. Verified exploitable through the real CLI before fixing. | **FIX** — routed through the existing `_toml_str()` escaper. |
| LA2 | HIGH | `config_is_live_ready()`'s placeholder-marker list missed the shipped example's Unichain V4 fields (`uniswap_v4_pool_manager`/`..._state_view` — neither starts with `YOUR_`/`0xWETH`/`0xUSDC`), so a user who fills every other placeholder is told the config is live-ready and `run --live` launches against a fake address. Verified empirically before fixing. | **FIX** — generalized to a principled check (any `0x`-prefixed token with a non-hex-digit character can only be a placeholder — real addresses are always exactly `0x`+40 hex digits), defending future placeholder shapes too, not just this one. |
| LA3 | MEDIUM | `payload.ensure_payload()` not safe under a concurrent double-launch of the `.exe` (double-clicking again because "nothing happened") — the loser raised an uncaught `FileExistsError`. Already caught by the existing crash net (not a silent vanish) but a needless scary traceback for a non-failure; `copytree` creates the destination as its first action so the loser fails atomically before writing anything. Module had zero prior test coverage. | **FIX** — catch and treat as "another instance already has this." |
| LA4 | LOW | `proc.run()`'s subprocess pipe was never explicitly closed — GC reclaims it eventually (not a real fd pileup) but left a `ResourceWarning` and untidy cleanup on the exception path (e.g. Ctrl-C mid-build). | **FIX** — `try/finally: proc.stdout.close()`. |

Re-verified present and correct at HEAD, with one gap closed in passing: the §9/§10 SIGTERM handler
was present and correct but had **zero test coverage** — added 3 tests including a real
delivered-signal test via `os.kill`. SIGKILL zombie reaping, startup-grace-on-restart, and
Popen-failure fd cleanup all re-confirmed correct and already covered. Recorded, not fixed (scope
decisions): `contracts/` is never prepared by `l2arb install` even though the dashboard's Contracts
panel needs `npx hardhat compile`; `fill_chain_endpoints` is fully-tested dead code (no CLI flag
reaches it — completing it is a feature decision); `proc.run()`'s build-time children don't share
`Service`'s process-group isolation (narrow impact, a real design tradeoff not a guess).

## Cross-chain execution

### What was run: the existing proof, re-confirmed live

`npm run test:fork:crosschain` against real `POLYGON_RPC_URL`/`ARBITRUM_RPC_URL` — **1 passing**, same
result shape as when originally built: Polygon source leg (real QuickSwap swap) → Arbitrum destination
leg (real Uniswap V3 swap), bridge simulated (no real `IBridgeAdapter` exists — see C1 below).

### What was built: a stronger, wallet-targeted proof

`contracts/scripts/live_cross_chain_fork.js` (new) — modelled directly on `live_flash_loan_fork.js`'s
precedent (§13) but for the two-leg, non-atomic cross-chain model. Key design correction made *during*
this session's build (recorded so a future session doesn't repeat the mistake): an early draft had the
"manipulator" dump WETH the executor never touched, proving nothing about the executor's own
economics. The corrected design has the **executor itself** trade on the dislocation:

1. **Setup (Polygon, source chain)**: a funded account organically swaps WMATIC for USDC.e (funds the
   executor's real starting inventory) and separately for WETH (via an undisturbed pool — no
   manipulation in this step).
2. **Manufactured dislocation**: that WETH is sold into the real Polygon WETH/USDC.e QuickSwap pool,
   suppressing WETH's USDC.e price — disclosed exactly like every other live-fork proof in this repo.
3. **Source leg**: the executor spends its real USDC.e inventory buying WETH at the now-cheap price
   (`executeSourceLeg`, real route through the dislocated pool), bridges it (simulated delivery, as in
   the existing test).
4. **Destination leg (Arbitrum)**: the executor sells the bridged WETH for USDC.e at Arbitrum's
   **real, untouched** Uniswap V3 price (`executeDestinationLeg` — no manipulation on this side).
5. **Sweep**: the guardian-gated `rescueTokens` delivers the final USDC.e to the real operator wallet
   — the actual operational mechanism this contract offers for extracting settled inventory.

**Result** (this run; every run re-manufactures its own dislocation so exact figures vary):

| | |
|---|---|
| Polygon forked at block | 91,787,150 |
| live WETH/USDC.e reserves | 983,900.97 USDC.e / 525.40 WETH |
| executor funded | 3,790.539863 USDC.e (organic swap, undisturbed pool) |
| dislocation | 22.52 WETH dumped into the live pool |
| source leg | bought 2.185768 WETH with the 3,790.54 USDC.e |
| Arbitrum forked at block | 493,162,228 |
| destination leg | sold 2.185768 WETH for 4,074.859969 USDC.e at Arbitrum's real, untouched price |
| **delivered to `0x50A71dF7DfC5850e8434C7c8A564366F4980183b`** | **4,074.859969 USDC.e** |
| **net** | **+284.320106 USDC.e** |

Sanity-checked before accepting the result: of the 2.185768 WETH bridged, the script's own honest
counterfactual (constant-product math against the pool's *pre-dump* reserves, for the identical
USDC.e input — no external price feed, nothing hardcoded) shows only 2.010349 WETH would have been
bought without the dislocation. Back-of-envelope, that undislocated amount sold at Arbitrum's same
real price would have netted roughly **−45 USDC.e** (a small loss — real fees/slippage across two
swaps, no free lunch) — confirming the reported +284.32 profit is genuinely, mostly attributable to
the disclosed manufactured dislocation, not an artifact of the measurement, and that this repo's
established "no fabricated profit, ever" bar holds for the new script exactly as it does for its
same-chain sibling.

7 new regression tests (`test/CrossChainLiveForkScript.test.js`) cover the new `assertDualForkOnly`
broadcast guard (network-must-be-`hardhat`, both RPC URLs required, network check ordered before the
RPC-URL check) and the chain address book. Wired into `package.json`'s `test` script (offline, no RPC
needed) and CI (the live run, RPC-secret-gated, mirroring `live_flash_loan_fork.js`'s CI step exactly).

**Honest reading, stated as plainly as the same-chain sibling states it**: both legs ran against real
forked live state — real pools, real reserves, real prices. The bridge is simulated (no real
`IBridgeAdapter` exists in this repo yet). The Polygon-side price was manufactured. This proves the
two-leg pipeline is fully operational end to end against live infrastructure on two chains and pays
the named wallet — it is **not** evidence of a standing, risk-free mainnet opportunity.

### Why a REAL (non-fork, broadcast) cross-chain execution was not attempted

Not merely "not authorized" — **not safely attemptable today regardless of authorization**, for one
dominant reason plus three compounding ones, any one of which is independently sufficient:

1. **No real bridge adapter exists.** `MockBridgeAdapter` — the only `IBridgeAdapter` implementation
   in this repo — "pulls tokens, emits an event, delivers nothing cross-chain" by its own design (it
   exists purely so the fork tests above can prove the *swap* mechanics without a live bridge relayer
   acting on an ephemeral fork). A real broadcast through it would pull real funds out on the source
   chain with **no delivery mechanism on the other side** — not a revert-and-lose-gas outcome like the
   same-chain case (§13), a **permanent, irreversible loss of the bridged funds**. This alone rules
   out any real attempt regardless of gas or deployment state.
2. **`CrossChainArbitrageExecutor` is deployed on zero chains.** Confirmed fresh this session
   (`contracts/deployments/` doesn't exist in this container) and consistent with §14's explicit
   `SKIP_CROSSCHAIN=1` during the Base deploy ("inert without a sibling deployment on a second chain,
   and there is no gas for one").
3. **Gas remains insufficient for a real second-chain deployment.** Re-confirmed fresh: Arbitrum
   0.0000023 ETH (dust), Optimism 0. Only Base and Polygon hold anything, and Polygon's ~0.091 POL is,
   per §14's own prior assessment (unchanged), roughly one deploy's worth with no operating headroom.
4. **No off-chain orchestrator exists** (C3, §12) — correctly out of scope, Phase-9-gated on
   human-set risk parameters the spec itself flags "NEEDS HUMAN."

Given reason 1 by itself, this was not treated as a judgment call needing the user's sign-off — it is
a hard technical safety fact, the same category as "there is nothing deployed to call" was in §13. The
strongest safe alternative — the new `live_cross_chain_fork.js` proof above — was built and run
instead. Building a real bridge integration (selecting Across/Stargate/CCIP/etc., a genuine security
review) is real, substantial, security-sensitive scope that deserves its own explicit conversation
with the operator, not a speculative same-session build.

## Net result

**14 confirmed defects fixed this session** (4 engine, 2 ingestion — including closing the core safety
property of the single most-severe item carried across two prior audit cycles, 4 dashboard, 4
launcher), each with a regression test, every gate independently re-run green by the orchestrating
session both before and after. One further ingestion gap (IN3, degenerate-zero pool seeding) was found
broader than previously scoped and correctly recorded rather than rushed. Tests: 884 → 954. Contracts
had no defects to fix — HEAD re-verified clean — so that component's session work was entirely the
execution proof, CI wiring, and docs described below. The cross-chain
dual-fork proof was re-confirmed live at HEAD, and a new, stronger proof
(`scripts/live_cross_chain_fork.js`) demonstrates the full two-leg cross-chain arbitrage flow —
buy-side dislocation capture, real bridge-adjacent swap mechanics on two independently-forked live
chains, and delivery of genuine, honestly-quantified profit to the real operator wallet — for the
first time in this repo's history, now with CI coverage behind the same RPC-secret gate its sibling
uses. What remains before a *real* cross-chain trade is possible is unchanged in kind from §12's
assessment, but sharper in its most important particular: it is not just "the orchestrator/bridge
work is Phase 9," it is "attempting one today, even successfully finding a route, would strand real
funds in a bridge adapter that delivers nothing" — the single fact that should anchor any future
session's urgency ordering.
