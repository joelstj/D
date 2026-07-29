# Research notes — Polygon + Arbitrum cross-chain flash loans, Yul optimization

Scratch notes for this build. Anchor doc per the TDD loop — if something built later
contradicts a finding here, stop and reconcile rather than pushing forward.

## Decision: which contract tree

Two parallel trees exist in `contracts/`:
- **Gen1** (`contracts/contracts/`, root `README.md`/`package.json` "L2_on-chain", solc 0.8.20,
  Hardhat primary + Foundry equivalent-sources): complete, working — `FlashLoanArbitrage.sol`
  (Aave V3 + Balancer V2), `CrossChainArbitrageExecutor.sol` (inventory-based, non-atomic),
  24 passing offline tests + an Arbitrum mainnet-fork suite. Polygon addresses already on file.
- **Gen2** (`contracts/src/`, `ralph/` autonomous-loop rewrite, solc 0.8.24, Yul/route-codec
  architecture): skeleton stage, Phase 0 unchecked in `ralph/BACKLOG.md`, deliberately scoped to
  5 L2s that exclude Polygon.

Confirmed via grep: **the two trees do not import from each other — fully independent.**
`script/Deploy.s.sol` already imports from `contracts/contracts/FlashLoanArbitrage.sol` (Gen1),
and CI (`contracts/.github/workflows/ci.yml`) only ever builds/tests Gen1 (`npm ci` → compile →
`npm test` → optional Arbitrum fork test). Gen2 is not wired into CI at all yet.

**Decision: extend Gen1.** It's real, tested, already supports Polygon in its address book, and
is what CI/root CLAUDE.md actually describes as "the contracts component." User did not respond
to the clarifying question before repeated interrupts signaled "proceed" — this is the
lower-risk, better-fit default. Gen2 remains untouched; it's a separate initiative for a future
session. Noted in contracts/README.md (not `ralph/PROGRESS.md`, which is Gen2's own memory and
would misleadingly suggest the loop advanced Gen2 tasks it didn't touch).

## Environment constraints (empirically confirmed, not assumed)

- No `forge`, no `node_modules`, no `lib/` in this sandbox — clean slate.
- `github.com` returns 403 through the agent proxy — **organization egress policy**, not a bug
  (`/root/.ccr/README.md`: "do not retry or route around it — report the blocked host"). This
  means `curl -L https://foundry.paradigm.xyz | bash && foundryup` cannot run here — Foundry's
  installer and source both live on GitHub. Matches `ralph/PROGRESS.md`'s own bootstrap note
  about this exact limitation in a sandboxed env.
- `registry.npmjs.org` is in the proxy's `noProxy` bypass list — reachable directly, confirmed
  200. **Hardhat path is fully usable here.**
- Public RPCs: `arb1.arbitrum.io/rpc` works (returned `0xa4b1` = 42161). `polygon-rpc.com`
  errors through this proxy (tenant/API-key gate — proxy-side, not necessarily broken for
  everyone). Working alternates confirmed: `https://polygon-bor-rpc.publicnode.com` and
  `https://polygon.gateway.tenderly.co` (both returned `0x89` = 137).
- **Consequence:** I can install and actually run Hardhat tests/fork tests myself in this
  session (real, verified green). I can write correct Foundry `.t.sol`/`.s.sol` files (same
  idioms as the existing ones) but cannot execute `forge` myself here — CI (GitHub Actions
  runners have full network access) or the user's own machine can. This mirrors exactly how
  this repo's own CI already treats Hardhat as the enforced gate and Foundry as "equivalent
  sources, provided" (no Foundry job in `ci.yml` today). I will not claim a forge run I didn't
  perform — Hardhat output is the real, pasted proof; Foundry files are written-but-unexecuted
  and clearly labeled as such.

## Address verification (Polygon, chainId 137)

From `contracts/config/addresses.js` / `contracts/addresses/addresses.json`, cross-checked
against training knowledge (all consistent/plausible; the file also correctly differentiates
Base's Uniswap router from the shared canonical address used elsewhere, which is a good sign
the data wasn't blindly copy-pasted):

| Contract | Address | Chains sharing it (per this file) |
|---|---|---|
| Aave V3 Pool | `0x794a61358D6845594F94dc1DB02A252b5b4814aD` | Optimism, Arbitrum, Polygon (canonical) |
| Balancer V2 Vault | `0xBA12222222228d8Ba445958a75a0704d566BF2C8` | All chains (canonical) |
| Uniswap SwapRouter02 | `0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45` | Optimism, Arbitrum, Polygon (Base differs) |
| QuickSwap V2 Router | `0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff` | Polygon-only |
| SushiSwap V2 Router | `0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506` | Arbitrum, Polygon (canonical) |
| WMATIC | `0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270` | Polygon |
| WETH (bridged) | `0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619` | Polygon |
| USDC (native) | `0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359` | Polygon |
| USDC.e (bridged) | `0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174` | Polygon |

**Real verification method: the fork test itself.** If any address were wrong, `getCode()` would
be empty or a call would revert against the wrong ABI — exactly what `ArbitrumFork.test.js`'s
`before()` hook already checks (self-skips if the fork doesn't have live code at the expected
address). I'll use the same self-verifying pattern for Polygon, using a Polygon-specific address
(WMATIC or the bridged WETH — NOT the Aave Pool, since that address is identical across chains
and wouldn't distinguish "is this actually a Polygon fork" from "is this an Arbitrum fork").

Need to find a real Polygon QuickSwap pair with a manufacturable dislocation, mirroring the
existing Sushi-on-Arbitrum manufactured-dislocation trick (dump ~50% of one pool's reserve into
a smaller pool, then capture the gap with the arb contract). WMATIC/USDC or WETH/USDC on
QuickSwap V2 are the natural pick (deep, well-known pairs).

## Cross-chain "flash loan" — what's actually true, and what we're proving

Both contract generations already document this correctly and I will not contradict it: **an
atomic cross-chain flash loan does not exist** — a transaction cannot span two chains, so
borrow-use-repay-atomically is only ever possible on one chain. `CrossChainArbitrageExecutor`
is deliberately **not** a flash loan — it's an inventory-based, two-transaction, non-atomic
model (`executeSourceLeg` on chain A, `executeDestinationLeg` on chain B), which is the honest
way real systems do this. "Successful cross-chain flash loan execution" cashes out to: a real
flash loan runs (and is proven) independently on each chain, AND the cross-chain
inventory+bridge flow is proven end-to-end across both chains' real forked state. Both are
already correctly labeled in-source; I'm extending proof, not changing the model.

`IBridgeAdapter` is a minimal interface (`bridge(token, amount, dstChainId, recipient,
options)`); `MockBridgeAdapter` (in `contracts/mocks/TestHelpers.sol`) already implements it by
pulling the token and holding it, emitting `Bridged`. The existing
`CrossChainArbitrageExecutor.test.js` only ever runs on one single Hardhat network/mock pool —
it does NOT prove anything across two real chains. That's the actual gap to fill.

**Plan to prove it for real, using tools I actually have:**
- Hardhat supports `hardhat_reset` (`network.provider.request({method:"hardhat_reset", params:
  [{forking:{jsonRpcUrl, blockNumber}}]})`) to re-point the SAME in-process chain at a different
  fork mid-test. This lets one test: (1) reset to a Polygon fork, deploy
  `CrossChainArbitrageExecutor`, fund it with real WMATIC/USDC inventory, run
  `executeSourceLeg` against a real QuickSwap pool, capture the bridged amount; (2) reset to an
  Arbitrum fork, deploy a fresh sibling executor (simulating the destination-chain deployment),
  mint/transfer the Arbitrum-native equivalent of the bridged value onto it (simulating what a
  real bridge/relayer delivers — this is the deliberately-mocked step, exactly per
  `docs/specs/10-cross-chain.md`: "messaging is mocked deterministically in tests"), run
  `executeDestinationLeg` against a real Arbitrum DEX pool. I can actually execute this myself.
- Foundry's native `vm.createFork`/`vm.selectFork` do the same thing more idiomatically (hold
  both forks in memory, switch between them). I'll write this version too since the user asked
  to use Foundry, but I cannot execute `forge` in this sandbox — this file will be correct and
  ready to run in CI or locally, clearly labeled as unexecuted-by-me.
- Not in scope for now (documented as follow-up): a real bridge protocol integration (Across /
  Stargate / LayerZero). Off-chain relayers can't act on ephemeral local forks anyway, so proving
  a *real* bridge needs live testnets, not mainnet forks — a materially different, bigger task.

## Yul optimization scope

Existing house style (already in the codebase, not invented by me): Yul is used for (a)
single-word/struct staticcall reads (`DexRouter.balanceOf`, `FlashLoanArbitrage._getReserves`/
`_token0`/`_token1`) and (b) pure integer math (`OptimalArbitrage.sqrt`). It is **never** used
for approval/transfer semantics (`SafeERC20.forceApprove`/`safeTransfer` stay untouched — OZ
handles USDT-like non-standard-return tokens correctly; hand-rolling that in Yul is a real
correctness risk for a marginal gas win) or admin paths (pause/roles/rescue — kept auditable).
`contracts/CLAUDE.md`'s own coding standard agrees: "Yul... on the hot path... only where it
measurably wins... every assembly block gets... a differential test against a reference
Solidity implementation." Applying "optimize each and every contract with Yul" as: every
contract/library that has a hot path gets it optimized, consistently, with a differential test
— not a blanket rewrite of one-off admin functions.

Concrete targets:
- **DexRouter.execute()**: convert the `if/else` DexType dispatch to a Yul `switch`; hand-encode
  the `UNISWAP_V2` and `UNISWAP_V3_SINGLE` call paths as raw `abi.encodeWithSelector`-equivalent
  Yul + `call()`, matching the low-level-call style the CURVE/GENERIC branches already use
  (skips Solidity's ABI-encoding memory allocation/copy overhead for the two hottest branches).
- **FlashLoanArbitrage.aavePremiumBps()**: convert to the same raw-staticcall Yul idiom already
  used for `_getReserves`/`_token0`/`_token1`, for consistency (single-word return, low risk).
- **CrossChainArbitrageExecutor._walkRoute()**: same loop-body pattern as
  `FlashLoanArbitrage._runRoute()` already benefits from (both call `DexRouter.balanceOf`, which
  is already Yul) — no new Yul surface beyond what the library already provides, confirmed by
  inspection; nothing further to add there safely.
- **OptimalArbitrage.optimalV2Amount()/getAmountOut()**: hand-roll the multiply/divide chains in
  Yul. Solidity 0.8.20 has default overflow checks; converting to raw Yul removes them, so I must
  either keep an explicit overflow check or prove it can't happen. Reserves are bounded well
  under 2^128 in practice (existing comment already notes real L2 pools are far below the
  ~1e38 pairwise-product ceiling) — I'll keep this honest with a fuzz test comparing the Yul
  version против the current plain-Solidity version across a wide input range, not just assume.
- Admin functions (`pause`/`unpause`/`rescueTokens`/`rescueETH`/role grants) stay plain Solidity
  in both contracts — not a hot path, and OZ's `AccessControl`/`Pausable` are themselves already
  well-optimized, audited code not worth re-deriving.

Every new assembly block gets: a comment on the memory/stack it touches (per existing style) and
a differential test asserting equality with a plain-Solidity reference across representative and
fuzzed inputs.

## Deployment scripts

Both `scripts/deploy.js` (Hardhat) and `script/Deploy.s.sol` (Foundry) are already
network-agnostic (read `config/addresses.js` / env vars respectively) and already work for
Polygon + Arbitrum with no changes needed for `FlashLoanArbitrage`. **Gap:** neither deploys
`CrossChainArbitrageExecutor` — only `FlashLoanArbitrage`. Need to add that deploy path to both
scripts for the cross-chain deliverable to be deployable, not just testable.

## Results (what actually got proven, and how)

Everything below was run for real in this sandbox except where marked "written, not executed."

- **Baseline (before any change):** `npm install` + `npm run compile` (31 files, 0 warnings) +
  `npm test` → 25/25 passing + `FORK_RPC_URL=<arbitrum> npm run test:fork` → 4/4 passing against
  live Arbitrum state. Confirms the starting point was real, not just README claims.
- **DexRouter Yul (UNISWAP_V2 + UNISWAP_V3_SINGLE hand-encoded calls):** found and fixed a real
  bug in my *own test* first (stale reserve snapshot across a multi-iteration sweep, not a
  contract bug — see the two-part debugging note in `test/DexRouter.test.js`'s git history/this
  session's transcript). After the fix: 6 new tests passing, including a differential sweep
  across 6 amounts, and the existing 4 Arbitrum fork tests still pass unchanged (same live
  SwapRouter02 + SushiSwap V2 contracts, encoding produced byte-identical results).
- **`aavePremiumBps`/`OptimalArbitrage.getAmountOut` Yul:** a 210-combination differential sweep
  (7 amounts × 5×5 reserves × 6 fees) against the existing JS reference formula, all exact
  matches; `aavePremiumBps` re-verified against the live Aave V3 Pool on both fork suites.
- **Polygon fork tests:** required real debugging against real failures, each one a genuine
  discovery, not a guess proven wrong in the abstract:
  1. `polygon-rpc.com` and `publicnode.com` both failed through this sandbox's proxy (a
     tenant/rate-limit gate and an anti-abuse block on bulk archival calls, respectively) —
     `polygon.gateway.tenderly.co` worked. Proxy/RPC-provider issue, not a contract issue.
  2. Polygon's WETH (`0x7ceB23fD...`) has no `deposit()` — it's a bridged ERC20, not a
     wrapped-native-gas-token contract (Polygon's gas token is MATIC, not ETH). Switched the
     test's asset to WMATIC, which is the correct analog and — bonus — Polygon's deepest pair.
  3. QuickSwap's WMATIC/USDC.e pool holds several million WMATIC (far more raw tokens than an
     equivalent-depth WETH pool would), so funding the manipulator needed `hardhat_setBalance`
     sized off the live reserve, with headroom kept strictly separate from the wrapped amount
     (an equal-buffer mistake left zero balance for gas on the first attempt).
  4. A 2%-of-reserve borrow blew through Uniswap V3's 0.3%-fee-tier concentrated liquidity for
     this pair (much shallower than QuickSwap's raw reserves), producing far less profit than
     the manufactured 50% dislocation implied. Reducing to 0.1% of reserve fixed it — confirms
     the contract logic was correct throughout; the sizing was the issue, empirically diagnosed
     rather than guessed at.
  Final result: 5/5 passing, real profit banked (2739 WMATIC on a 3593 WMATIC loan) via both
  Balancer V2 and Aave V3, against live QuickSwap + Uniswap V3 Polygon contracts.
- **Cross-chain dual-fork proof:** 1/1 passing on the first run after fixing a bug caught before
  running it (checking a Polygon-fork balance after already `hardhat_reset`-ing to Arbitrum,
  which would have silently queried the wrong chain's state). Real source leg on a live Polygon
  fork (QuickSwap WMATIC→WETH), simulated bridge delivery (documented as such, not hidden),
  real destination leg on a live Arbitrum fork (Uniswap V3 WETH→USDC.e).
- **Foundry files** (`test/foundry/PolygonFork.t.sol`, `test/foundry/CrossChainDualFork.t.sol`):
  written using the exact parameters proven via the Hardhat runs above, but **not executed** —
  `forge` cannot be installed in this sandbox (GitHub is blocked by org egress policy, confirmed
  via `/root/.ccr/README.md`'s guidance: "do not retry or route around it"). CI (GitHub Actions
  has full network access) or the user's own machine should run these to get a live Foundry-side
  confirmation; they were not fabricated as passing.
- **Full final regression** (after all batches): 33/33 offline, 4/4 Arbitrum fork, 5/5 Polygon
  fork, 1/1 cross-chain dual-fork — all green, all real output pasted into this session's
  transcript and into `README.md`/`docs/DEPLOYMENT.md`.
