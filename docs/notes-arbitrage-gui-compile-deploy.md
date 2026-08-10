# Research notes — arbitrage GUI compile/deploy + Yul + config completeness (2026-08-10)

Branch `claude/arbitrage-gui-compile-deploy-0cvfjn`. Scratch/anchor doc per the TDD loop — if
something built later contradicts a finding here, stop and reconcile rather than pushing forward.

## Task (verbatim)

"Please make sure that there are fully functional and error free 'compile' and 'deploy' buttons in
the GUI that are live wired to the backend that can compile and deploy the different contracts
needed for cross chain arbitrage and same chain arbitrage. Make sure the GUI prompts the user to
enter in their wallet private key and wallet address for pre-authorized transactions signing and
automatic deposits of the profits from any successful flash loan smart contract trades so that they
are deposited directly into the users connected metamask wallet and not held in the contracts. Also
ensure all contracts are optimized with Yul. And that when the .exe is launched the config.toml is
completely adding all the necessary pools and needed information and please add more tokens to the
cross chain arbitrage, we should have at least 15-20 tokens instead of just two."

## Decision: declining the private-key-entry piece (recorded, not implemented)

A GUI field for the user's raw private key, used for "pre-authorized" (i.e. no per-transaction
confirmation) automatic signing, is **not being built**. This is hot-key custody in a web backend —
the exact architecture root `CLAUDE.md` §2 invariant 3, §10, §12, and §13 have deliberately built
*against*, repeatedly, across six prior audit sessions ("the backend never holds a key and never
broadcasts; every on-chain write is signed by the operator's MetaMask"). It is also structurally
identical to credential-phishing/drainer UI patterns regardless of intent. Per §2's own instruction
("stop, do not implement it, and record the concern rather than faking it"), this is recorded here
rather than built.

**The underlying goal is already met without it.** `FlashLoanArbitrage.sol:294` and
`CrossChainArbitrageExecutor.sol`'s settlement path already default `profitReceiver` to the
transaction signer when unset — profit lands in the connected wallet automatically, within the same
atomic transaction, with MetaMask doing real per-transaction signing (the key never leaves the
wallet). "Pre-authorization" in this architecture means the operator connects once via MetaMask;
each write still gets a real (fast, one-click) confirmation, which is the safety property, not
friction to be engineered away. Nothing in this session adds private-key handling anywhere in
`dashboard/`.

## Research findings (4 parallel Explore audits, all read-only, verified against current HEAD)

### A. Dashboard Contracts panel (compile/deploy) — already architecturally correct, real bugs found

- Both `FlashLoanArbitrage` and `CrossChainArbitrageExecutor` already have working, independent,
  MetaMask-signed compile+deploy flows (`ContractsPanel.tsx` `onDeploy`/`onDeployCrossChain`) wired
  to real backend endpoints (`dashboard/backend/src/contracts/{routes,service}.ts`). Confirmed:
  ctor arg order matches Solidity exactly; zero private-key/signer construction anywhere in
  `dashboard/` (grep-verified); `envFile.ts` explicitly refuses to ever write `PRIVATE_KEY`.
- **Bug (real, fixable): cross-row deploy concurrency desync.** `ContractsPanel.tsx`'s
  `busy.deploying` is a single shared string keyed by network only. Deploying network A, then
  clicking deploy on network B while A's tx is still pending, overwrites the shared field — A's
  button silently re-enables and its spinner vanishes while its transaction is still in flight,
  allowing a double-submit. Fix: key busy state per (network, contract) pair, e.g. a `Set<string>`.
- **Gap: the real wagmi-wired handlers have zero test coverage.** `ContractsPanel.test.tsx` only
  exercises the pure-props `ContractsPanelView`; `onDeploy`/`onDeployCrossChain`/`onCompile`
  (the actual `useDeployContract`/`useSwitchChain`/API-call wiring) are untested.
  `WalletButton.test.tsx` already establishes the `vi.mock("wagmi", …)` pattern to reuse.
- **Environment gap:** no `node_modules` anywhere (`dashboard/`, `dashboard/backend/`,
  `dashboard/frontend/`, `contracts/`) in this sandbox — nothing has been installed. Must
  `pnpm install`/`npm install` before any gate can actually be run, and before the Compile button's
  real dependency (`npx hardhat compile` in `contracts/`) can be trusted.
- Minor: `onDeployCrossChain` captures the atomic contract address at click time; a concurrent
  atomic redeploy between click and completion could record a stale pairing. Same root cause as the
  busy-state bug; likely resolved by the same fix (serializing per-row actions).
- `profitReceiver` is a pure Solidity-level default — not a constructor arg, so deploy needs no
  change for it. No live-execute UI exists anywhere yet (`LiveExecutor.execute()` unconditionally
  throws, by design — invariant 3), so there is nothing to wire it into; out of scope here.

### B. Contracts Yul coverage — 10 functions already optimized (not 5), one strong new candidate

- `CLAUDE.md` undercounts: **10** functions already have hand-written, tested, house-style Yul
  (`DexRouter.execute` GENERIC-branch patch, `balanceOf`, `_swapUniswapV2`, `_swapUniswapV3Single`,
  `OptimalArbitrage.getAmountOut`, `.sqrt`, `FlashLoanArbitrage.aavePremiumBps`, `_getReserves`,
  `_token0`, `_token1`), each with differential tests against a reference Solidity/JS implementation
  (`DexRouter.test.js`, `OptimalArbitrage.test.js`).
- **`CrossChainArbitrageExecutor.sol` has zero Yul** — the one real gap matching the task's "all
  contracts" framing. Its own NatSpec already reasons about this ("considered, not skipped").
- **Best candidate:** `_walkRoute`'s per-hop `SwapStep memory step = steps[i]` (line ~219) — a real
  calldata→memory struct copy (including the dynamic `bytes data` field) inside the file's own
  declared hot loop, called every hop from both `executeSourceLeg` and `executeDestinationLeg`.
  Matches the project's own gas/Yul spec (`contracts/docs/specs/07-gas-and-yul.md`) "Route
  decoding" bullet almost verbatim.
- **Secondary candidate (larger blast radius, lower priority):** `FlashLoanArbitrage._settle`'s
  `abi.decode(params, (ArbParams, uint256, address))` — O(route length) once per tx rather than
  per-hop; would also require reworking `_runRoute`/`DexRouter.execute` to consume calldata offsets.
  Gate on a committed gas-snapshot delta before committing to it (house rule: "never on vibes").
- **Explicit do-not-convert** (documented reasoning, matches existing project convention):
  `pause`/`unpause`/allowlist setters/`rescueTokens` (low-frequency `GUARDIAN_ROLE` ops — auditability
  > gas), the route-contiguity/deadline/asset-match safety loop in `executeArbitrage` (the exact
  fund-safety check added after a real drain vector, root `CLAUDE.md` §9 item 2), OpenZeppelin
  `AccessControl`/`ReentrancyGuard`/`Pausable` internals (trusted, audited library code).
- House style (from `07-gas-and-yul.md` + every existing block): gas-snapshot-justified, not vibes;
  explicit byte-offset comments for multi-word calldata; selectors from `.selector` or a commented
  hex literal; every `call`/`staticcall` checks success + `returndatasize()` and bubbles the real
  revert; a NatSpec paragraph stating Yul scope, including *why not* for skipped functions; a
  differential test against a reference Solidity/JS implementation.
- **Caveat found:** `contracts/test/ArbExecutor.t.sol` (12 Foundry tests) is *not* coverage for the
  active contracts — it targets a dormant, unrelated `contracts/src/core/ArbExecutor.sol` scaffold
  (Ralph loop, Phase 0). Ignore it when reasoning about Yul test coverage for `FlashLoanArbitrage`/
  `DexRouter`/`CrossChainArbitrageExecutor`.

### C. Launcher config.toml generation — currently generates nothing; 4 of 5 chains have zero pools

- `launcher/l2arb/config.py::ensure_config_toml()` does a **verbatim copy** of
  `ingestion/config/config.example.toml` — no pool generation, no discovery, ever.
- `l2arb setup`'s "live-ready" path (`setup.py::write_arbitrum_quickstart`) is **Arbitrum-only**
  (asserted by its own test, `test_setup.py:94`, `cfg.count("[[chains]]") == 1`) and its pool list
  comes from copying `ingestion/config/pools/arbitrum.example.toml` verbatim — 2 pools, one pair.
- **Base/Optimism/Unichain/Ink have no pool file at all**, not even a template — their
  `pool_registry` path in the example config points at files that don't exist anywhere in the repo.
- **No pool-discovery code exists** in Rust or Python — only a prose TODO in
  `ingestion/config/pools/README.md` ("a discovery script can seed them from factory events").
- Target matrix (root `CLAUDE.md` §1): 5 chains — Arbitrum 42161, Base 8453, Optimism 10,
  Unichain 130, Ink 57073. Pool schema is DEX-shape-based (`kind = v2|v3|v4`), not brand-specific.

### D. Cross-chain token config — currently 0 usable assets, not 2 (all addresses are placeholders)

- `ingestion/config/config.example.toml`'s `[cross_chain]` block has exactly 2 symbols (WETH, USDC),
  and **every single address is a literal placeholder string** (`"0xWETH_ARB"` etc, not real hex).
  `build_cross_chain` silently drops unparseable addresses, and `filter_cross_chain` additionally
  requires a matching `[[cross_chain.bridges]]` row (USDC has none) — net result today is **0**
  usable cross-chain assets, confirming root `CLAUDE.md` §12 item 3's "silently inert" framing goes
  further than stated: it's not just fragile, it's currently empty.
- The engine (`engine/src/l2arb/model/canonical_asset.py`) has no hardcoded tokens at all — it
  rebuilds `AssetRegistry` fresh from ingestion's config on every `/detect` call. **Ingestion's TOML
  is the single source of truth**; fixing it there is sufficient, nothing to change in the engine's
  registry logic itself (only its test fixtures, if they assume the 2-token placeholder shape).
- `contracts/config/addresses.js` already has **real, verified** addresses reusable here: WETH on
  all 5 target chains (Base/Optimism/Unichain/Ink share `0x4200...0006`; Arbitrum
  `0x82aF49447D8a07e3bd95BD0d56f35241523fBab1`), USDC on 3/5 (Arbitrum
  `0xaf88d065e77c8cC2239327C5EDb3A432268e5831`, Base `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913`,
  Optimism `0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85`) — Unichain/Ink USDC and every other symbol
  need fresh, sourced research (next section).
- Per-entry schema: one `[[cross_chain.assets]]` block (symbol + per-chain
  `{chain_id, address, decimals, native, bridgeable}`), plus one `[[cross_chain.bridges]]` row per
  *directed* route actually usable (`symbol, from_chain, to_chain, fee_bps, fixed_fee,
  settle_seconds`) — a symbol with representations but no bridge row is correctly pruned, so bridge
  rows must be added deliberately per real route, not assumed.

## Build plan

1. Install dependencies (`dashboard` pnpm install, `contracts` npm install) to get a real green
   baseline before touching anything.
2. Dashboard: fix the busy-state concurrency bug (key by network+contract, not network alone); add
   tests for the real wagmi-wired deploy/compile handlers using the established mock pattern.
3. Research real, sourced token addresses across the 5 chains (target 15-20 symbols; only include
   what's genuinely verifiable — no padding to hit a number) and real DEX factory/pool addresses per
   chain — both via WebSearch/WebFetch against authoritative sources (official docs, Circle's
   address list, block explorers), every address cited.
4. Wire the verified token list into `ingestion/config/config.example.toml` `[cross_chain]`
   (assets + matching bridge rows) and mirror new tokens into `contracts/config/addresses.js`;
   update/add tests in `engine/` and `ingestion/` that exercise the expanded set.
5. Expand `ingestion/config/pools/<chain>.toml` for all 5 chains using the verified pool addresses,
   and extend the launcher's setup/install path to generate a real multi-chain config instead of
   copying the Arbitrum-only quickstart — with tests.
6. Yul-optimize `CrossChainArbitrageExecutor._walkRoute`'s per-hop calldata access, following the
   documented house style, with a differential test against the existing Solidity behavior and a
   gas-snapshot comparison. Evaluate the secondary `_settle` candidate if time/risk allow.
7. Full-suite regression run across engine/ingestion/contracts/dashboard/launcher (anti-hallucination
   + audit pass); update root `CLAUDE.md` and component docs; commit, push, open/update the PR.

Scope note: a full dynamic on-chain pool-discovery engine (querying DEX factories live at
setup-time) would be the most future-proof answer to "completely adding all the necessary pools,"
and the ingestion README already gestures at it — but it's a substantial net-new RPC-calling
subsystem in its own right. Given the size of this task already, step 5 above ships a real,
verified, *curated* multi-chain pool set (a large improvement over today's "1 of 5 chains, 2
pools") rather than building the discovery engine; that remains a good, honestly-recorded follow-up
rather than something to rush.

## Yul optimization outcome — investigated, measured, reverted (not a gap left open)

The obvious candidate (per the Contracts-Yul research above) was `CrossChainArbitrageExecutor
._walkRoute`'s per-hop `SwapStep memory step = steps[i]` — a real calldata-to-memory struct decode,
unlike `FlashLoanArbitrage._runRoute`'s equivalent line (a free pointer copy, since its `steps` is
already memory from `abi.decode`). This was concretely implemented, not just reasoned about:
`DexRouter.execute` was refactored from one `SwapStep memory` parameter to twelve scalar
parameters, so each caller (calldata or memory) passes only the fields it already has, with no
struct materialisation — updated at all three call sites (`CrossChainArbitrageExecutor._walkRoute`,
`FlashLoanArbitrage._runRoute`, `TestHelpers.sol`'s `DexRouterHarness`).

**Measured, not assumed**, via `git stash` A/B on an otherwise-identical commit, a real 2-hop
`executeDestinationLeg` call, run twice each way to confirm reproducibility: **161,240 gas before,
162,209 gas after — a reproducible +969 gas regression**, not a win. All 66 offline tests passed
unchanged in both states (no test files were modified), which is exactly what made the regression
trustworthy rather than a fluke: the behavior was identical, only the cost went up. Root cause: the
struct-decode savings were real but smaller than the added cost of marshalling twelve stack
arguments across the internal-call boundary instead of one struct pointer, for the common case
(V2/V3_SINGLE hops, where `data` is empty and there was little decode cost to save in the first
place).

**Reverted rather than shipped**, per the project's own rule
(`contracts/docs/specs/07-gas-and-yul.md`: "optimize against a benchmark, never on vibes... if a
change doesn't move the gas snapshot... it doesn't ship"). `CrossChainArbitrageExecutor.sol`'s
Yul-scope NatSpec now records this specific attempt and its measured result, so a future session
doesn't re-derive and re-attempt the same losing change. **Net conclusion: `CrossChainArbitrageExecutor.sol`
has no available Yul optimization that measurably wins beyond what it already inherits from
`DexRouter`'s existing hand-optimised `balanceOf`/`_swapUniswapV2`/`_swapUniswapV3Single`** — the
same "not worth the added risk" conclusion this codebase already reached for `UNISWAP_V3_MULTI`,
now reached the same rigorous way for this candidate too.
