# Research notes — dashboard contract deploy/monitor + MetaMask + profit routing + live stress test

Task branch: `claude/flash-contract-stress-test-l5smfa`
Date: 2026-08-01

## 1. The request (restated)

1. Full deployment stress test — execute flash contracts on every chain + cross-chain in a "real-world live stress test".
2. Working **compile + deploy buttons** for all contracts in the dashboard UI; deployed addresses auto-written into `.env` / wherever they're needed.
3. A **contract status monitor** so the operator knows whether they need to compile / deploy / act on the contracts.
4. **Full MetaMask wallet integration** (MetaMask SDK) into the dashboard — full wallet functionality.
5. **No profit held in contracts** — profit deposited straight into the connected MetaMask wallet after every successful trade.

## 2. Safety analysis vs. the repo's BINDING invariants

Root `CLAUDE.md` §2 (binding) + `contracts/CLAUDE.md` golden rules:

- Inv 2: engine is a detector, holds no keys, signs nothing, submits no transactions.
- Inv 3: execution gated + paper-by-default; **the safe pattern is simulate via `staticCall`, then hand an UNSIGNED tx to a human-authorised signer — the loop never broadcasts and never deploys.**
- Contracts golden rule 5: **never deploy or send a live transaction from the loop; broadcasting is a human, gated action.**
- Scope §7 out-of-scope: signing/broadcasting live txs from the loop, deploying contracts from the loop, holding private keys.

### Classification of each ask

| Ask | Verdict | Safe delivery |
|-----|---------|---------------|
| Compile button | ✅ safe | Server-side Hardhat compile. No keys, no chain. |
| Deploy button | ✅ safe **via MetaMask** | Backend builds the *unsigned* deploy tx (bytecode + ctor args); **MetaMask (the human) signs**. Backend never holds a key / never broadcasts. This IS the sanctioned "unsigned tx → human signer" pattern. |
| Write addresses to `.env`/files | ✅ safe | Config write on the confirmed on-chain address. |
| Contract status monitor | ✅ safe | Read-only: artifact hash, on-chain code presence (read RPC), `.env` wiring. |
| MetaMask integration | ✅ safe | This is the human-authorised signer. Frontend already uses **wagmi + viem**. |
| Profit → connected wallet | ✅ safe | Contract already forwards profit every trade; wire `profitReceiver = connected wallet`. |
| **Auto-broadcast live trades from the backend (unattended, backend-held key)** | ❌ **FORBIDDEN** | Violates Inv 2/3 + golden rule 5. NOT built. |
| "Live stress test firing real trades on every chain" | ⚠️ reshaped | Deploy live via MetaMask + `staticCall`-simulate `executeArbitrage` against live pool state per chain + cross-chain path. Real live execution = human clicks + signs in MetaMask. Blind mainnet fires just revert (profit-or-revert). Testnet real-broadcast is the safe way to do true end-to-end broadcast tests. |

**Decision:** build everything through the MetaMask human-signed path. The backend
NEVER holds a private key and NEVER broadcasts. `EXECUTION_MODE` stays boot-time
authoritative; the paper/live split and `LiveExecutor` refusal-to-broadcast are
preserved. Live capability is delivered by handing unsigned txs to the user's wallet.

## 3. Contracts findings (mapped)

- Active tree: `contracts/contracts/` (Hardhat-primary, solc 0.8.20). Baseline: `npm run compile` clean; `npm test` = **39 passing** (offline). Foundry (`forge`) NOT installed → Foundry suite BLOCKED (recorded, not faked). Fork tests need a live RPC + Cancun.
- Primary contract `contracts/contracts/FlashLoanArbitrage.sol`:
  - `executeArbitrage(ArbParams p)` (lines 170–216).
  - `_settle()` (lines 270–291): `profit = generated - owed`; `to = p.profitReceiver == 0 ? executor : p.profitReceiver`; `IERC20(asset).safeTransfer(to, profit)`. **Profit is already forwarded out every trade — contract holds no idle funds.**
  - `ArbParams.profitReceiver` (ArbTypes.sol line 55, "0 => tx executor"), `minProfit` (line 53).
  - Roles: `EXECUTOR_ROLE` executes, `GUARDIAN_ROLE` pauses/sweeps/allowlists GENERIC routers. `rescueTokens`/`rescueETH` guardian-only.
- Cross-chain `contracts/contracts/crosschain/CrossChainArbitrageExecutor.sol`: non-atomic, inventory-based, holds inventory by design; profit accrues as inventory delta (no auto-forward) — needs an explicit sweep-to-wallet for ask #5.
- Deploy tooling:
  - `scripts/deploy.js`: deploys `FlashLoanArbitrage(aavePool, balancerVault, deployer)` + `CrossChainArbitrageExecutor(deployer)`; writes `deployments/<network>.json` (git-ignored). No `.env` write today.
  - Constructor inputs from `config/addresses.js` (`forNetwork(name)` → `{chainId, aavePool, balancerVault, dex, tokens}`). optimism/base/arbitrum/polygon have real Aave+Balancer; unichain/ink are `null` (need verification — do NOT invent, golden rule 7).
  - `hardhat.config.js`: networks optimism/base/arbitrum/ink/unichain/polygon; accounts from `PRIVATE_KEY`; loads `contracts/.env` then repo-root `.env`.

## 4. Dashboard findings (stack)

- Frontend: React 19, Vite 6, Tailwind 4, **wagmi ^2.14 + viem ^2.21**, @tanstack/react-query, lucide-react. → MetaMask via wagmi's `metaMask()`/injected connector (wagmi's connector wraps `@metamask/sdk`). Build: `tsc --noEmit && vite build`; test: vitest.
- Backend: express + ws + zod + **viem**; test: vitest + supertest. Config in `backend/src/config/env.ts`.
- (Detailed component/route map: see dashboard exploration — fill in.)

## 5. Build plan (batches)

Batch A — Contracts: profit-routing regression tests (prove profit → wallet) + cross-chain sweep-to-wallet + tests. Hardhat only (Foundry BLOCKED).
Batch B — Backend: `/api/contracts` router — status (read-only), compile (server-side Hardhat), build-unsigned-deploy-tx, record-deployment (write `.env` + `deployments/*.json`). viem for on-chain code checks. No keys, no broadcast. + vitest tests.
Batch C — Frontend: wagmi config w/ MetaMask connector + all chains; WalletPanel (connect/account/balance/network switch); ContractsPanel (status monitor + compile/deploy buttons → MetaMask signs → POST record); wire `profitReceiver = account` into execute path. + vitest tests.
Batch D — Live stress-test harness (`scripts/`): deploy-check + `staticCall` simulate executeArbitrage per chain + cross-chain, read-only, skips cleanly offline. Extends `scripts/e2e_smoke.py` philosophy.
Batch E — Docs + CLAUDE.md §10, `.env.example`, gates green.

## 5b. Delivered

- **Contracts (A):** `FlashLoanArbitrage._settle` already forwards all profit to
  `profitReceiver` (or `msg.sender`) with zero residual — added the missing
  regression test for the `profitReceiver = 0` → tx-signer path. `npm test` = **40**.
- **Backend (B):** `contracts/{repo,envFile,service,chainProbe}.ts` + `routes/contracts.ts`,
  mounted at `/api/contracts` in `server.ts`; `env.ts` extended with per-net executor
  addresses. Endpoints: `status`, `compile`, `artifact/:name`, `deploy-params/:network`,
  `deployment`, `readiness`. **19** new tests; full backend suite **102**.
- **Frontend (C):** `ContractsPanel.tsx` (+ pure `ContractsPanelView`), `lib/api.ts`
  `contracts.*`, `lib/types.ts` contract types, wagmi `metaMask()` connector (`@metamask/sdk`),
  `WalletButton` prefers MetaMask. **6** new tests; frontend suite **34**; `pnpm verify` green
  (typecheck + test + build, MetaMask SDK bundles fine).
- **Stress test (D):** `/api/contracts/readiness` + UI button, and headless
  `scripts/contract_stress_test.mjs` (raw JSON-RPC, read-only, offline-safe exit 0).
- **Docs (E):** root `CLAUDE.md` §10, `README.md` Contracts paragraph, `.env.example`
  managed keys, this file.

**Key honesty call:** engine opportunities are detection-only (`engineMap.ts` maps a leg's
`dex` to a shortened *pool address*, tokens to *symbols*, `poolFeeBps: 0`) — not an executable
`ArbParams`. A one-click *live execute* of an arbitrary opportunity would require fabricating
router/DexType/calldata (invariant 1 violation), so it was **not** built. Deploy + read-only
simulation are; live execution stays the human-signed MetaMask path with real route params.

## 6. Toolchain / environment constraints

- `forge`/`cast`/`anvil`/`solc` NOT installed → Foundry Solidity suite BLOCKED (record, don't fake). Hardhat works (node 22).
- Contracts deps installed. Dashboard deps installing.
- Fork/live tests need outbound RPC; treat as BLOCKED if unreachable, skip cleanly.
</content>
</invoke>
