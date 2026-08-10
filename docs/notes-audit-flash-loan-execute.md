# Research notes — production audit + live flash-loan execution (2026-08-09)

Branch `claude/audit-flash-loan-execute-qkyth2`. Scratch/anchor doc per the TDD loop — if
something built later contradicts a finding here, stop and reconcile rather than pushing forward.

## Task

"Run a granular production grade audit and fill any gaps and try to live execute one flash loan
and deposit it into the environments metamask wallet address."

## Environment (established empirically, not assumed)

This is the first session where the container carries **real operator credentials**. Established
by direct probe, before any code was read:

| Fact | Value | How verified |
|------|-------|--------------|
| Operator key present | `EXECUTOR_PRIVATE_KEY` (env var, not in git) | `env \| cut -d= -f1` |
| Derived address | `0x50A71dF7DfC5850e8434C7c8A564366F4980183b` | `viem` `privateKeyToAccount`; key never printed |
| Nonce on Polygon | 306 | `eth_getTransactionCount` |
| Balance — Polygon | 0.091143 POL | `eth_getBalance` |
| Balance — Base | 0.003469 ETH | `eth_getBalance` |
| Balance — Arbitrum | 0.00000234 ETH (dust) | `eth_getBalance` |
| Balance — Optimism | 0 | `eth_getBalance` |
| Polygon RPC | reachable (Alchemy, from `POLYGON_RPC`) | `eth_blockNumber` → block 91,469,463 |
| Arbitrum / Base public RPC | reachable | `eth_blockNumber` |
| `forge` / Foundry | NOT installed (consistent with every prior session) | `command -v forge` |
| `contracts/deployments/` | **does not exist** — this repo has zero deployed contracts on any chain | `ls` |

`ARB_CONTRACT_ADDRESS=0x6BF1f950A060F33c714AE144cE27D547c8AA3a32` is set and *does* hold bytecode
on Polygon, but it belongs to a **different project** (the `money_bot`/CROOK3D workspace whose
skills are also mounted here), not to `D`. It is not a `FlashLoanArbitrage` deployment and is not
treated as one anywhere in this session.

## Framing of the "live execute" half — the honest boundary

The request has two halves. The audit half is unambiguous. The execution half runs into three
independent walls, all of which were confirmed rather than assumed:

1. **Binding repo constitution.** Root `CLAUDE.md` §2 invariant 3 and §10, plus
   `contracts/CLAUDE.md` golden rule 5, all state the same rule in different words: *the loop
   never broadcasts and never deploys; every on-chain write is signed by a human's MetaMask; the
   backend never holds a key.* A mainnet `executeArbitrage` broadcast from `EXECUTOR_PRIVATE_KEY`
   is precisely the forbidden action, and §2 instructs: "stop, do not implement it, and record the
   concern rather than faking it."
2. **No deployed contract.** There is no `FlashLoanArbitrage` on any chain for this repo, so there
   is nothing to call. Broadcasting a real deployment is itself a forbidden write.
3. **Profit-or-revert + no route data.** `executeArbitrage` reverts unless net profit ≥ `minProfit`.
   Firing it without a genuinely profitable, fully-specified route (router addresses, `DexType`,
   calldata) just burns gas. Root `CLAUDE.md` §10/§12-E2 already record that the engine emits
   **detection** data, not a constructible route — so no such route can be produced today without
   fabricating it (invariant 1).

The user was asked which of three paths to take (fork-execute / deploy-only / full broadcast) and
did not answer. Default taken: **the strongest execution proof that breaks no invariant and risks
no funds** — execute a real flash loan through the real Aave V3 pool and real DEX pools, forked at
the live head block, with `profitReceiver` set to the operator's actual wallet, and assert the
profit lands there. What that does and does not prove is stated explicitly in the deliverable
itself. The mainnet-broadcast decision is left with the user.

## Baseline (reconfirmed green before any change)

| Gate | Result |
|------|--------|
| engine `make check` | **460 passed**, 99.87% coverage |
| ingestion fmt + clippy + test | **204 passed** across 22 suites, clippy clean |
| contracts Hardhat offline | **48 passing**, 10 pending (fork suites self-skip) |
| dashboard `pnpm verify` | typecheck + **126 backend** + **45 frontend** + both builds |
| launcher unittest | **80 passed** |
| contracts Foundry | **BLOCKED locally** — `forge` not installed in this sandbox. Recorded, not faked (as in §9/§11/§12). Note it *does* run in CI via `foundry-toolchain@v1` (16 tests green on this PR's run) — only its fork-gated step is dark, for want of a secret. |

### Newly unblocked this session

Both Hardhat mainnet-fork suites were run against **live chain state** — the first time either has
executed in this repo's history (every prior session recorded them as unrunnable):

- `test/fork/PolygonFork.test.js` — **5 passing**. Real Aave V3 + real Balancer V2 flash loans,
  real Uniswap V3 `SwapRouter02` + real QuickSwap V2 swaps, live QuickSwap WMATIC/USDC.e reserves.
  Captured 1623.66 WMATIC (Balancer) / 1621.94 WMATIC (Aave, net of the live premium) on a
  3442.05 WMATIC loan.
- `test/fork/ArbitrumFork.test.js` — **4 passing**. Real Balancer V2 + Aave V3, real Uniswap V3 +
  SushiSwap V2. Captured 0.9283 WETH (Balancer) / 0.9279 WETH (Aave) on a 0.7915 WETH loan.

Both suites manufacture the price dislocation deliberately (documented in their own headers), so
they prove *the contract is fully operational against live infrastructure* — not that free profit
is sitting on mainnet. That distinction is preserved everywhere it is reported.

## Findings

Severity is "how badly this blocks or misreports a real, successful flash-loan execution."
FIX = addressed this session with a regression test. RECORD = confirmed real, deliberately not
fixed, with the reasoning stated.

| # | Sev | Component | Finding | Disposition |
|---|-----|-----------|---------|-------------|
| A1 | MEDIUM | contracts | `ArbitrageExecuted` logs `p.profitReceiver` **raw** (`FlashLoanArbitrage.sol:290`), not the effective recipient. On the default path — `profitReceiver == address(0)`, which `_settle` resolves to the tx signer — the event records `0x0` as the indexed receiver while the funds go to the signer. The contract's own comment says "Historical PnL lives in logs, not storage," so every downstream indexer/PnL attribution mis-attributes exactly the path root `CLAUDE.md` §10 item 3 advertises as the headline feature ("profit straight to your connected wallet by default"). The field is `indexed`, so a topic filter for "arbitrage that paid me" returns nothing. The existing regression test (`FlashLoanArbitrage.test.js:138`) asserts *balances* but never the emitted event — which is why it hid, the same "no test composed the two layers" shape as §11 item 3. | **FIX** |
| A2 | MEDIUM | CI | The Hardhat fork suites are the repo's **only** working live-execution proof, and nothing runs them. `npm test` pins 4 offline files; `test:fork*` are separate scripts invoked by hand. CI's one fork hook (`ci.yml:110`) gates on an `ARBITRUM_RPC_URL` secret that has never been configured and points at the **Foundry** suite, so it has never run either. So the proof that the executor works against live chains has zero automated coverage, behind a CI step that looks like it provides some. | **FIX** |
| A3 | MEDIUM | contracts | `quoteOptimalTwoHopV2` accepts `feeBpsBuy` **and** `feeBpsSell` and documents both as per-pool fees, but passes only `feeBpsBuy` into `OptimalArbitrage.optimalV2Amount`, whose own signature documents its single `feeBps` as "applied on both hops." The *profit estimate* then correctly uses each pool's own fee. Net effect: for an asymmetric-fee pair (very common — a 0.30% V2 pool against a 0.05% V3-style pool) the returned "optimal" loan size is optimal for a pair that does not exist, so the caller systematically under- or over-borrows. Not a fund-safety issue (`minProfit` is still the hard guard) but a real correctness gap in an advertised sizing feature. | **FIX** |
| A4 | LOW→n/a | product | No operator-facing surface anywhere in `dashboard/`, `engine/`, or `launcher/` sets `profitReceiver` — grep confirms the identifier exists only in `contracts/` and its own tests/examples. Initially looked like a wiring gap on the user's exact ask. **On inspection it is not a defect to fix:** the dashboard has no live-execution path at all (by design, invariant 3), so a "where does profit go" setting there would be dead config — precisely the defect class §8 item 2 already had to remove. The code that *does* build `ArbParams` — `contracts/integration/examples/bot.js:59` and `bot.py:52` — already sets `profitReceiver` to the signer's own address, correctly. | **RECORD** (no change; documented so the next session doesn't "fix" it into dead config) |
| A5 | — | contracts | Reviewed for fund-safety regressions against current HEAD rather than trusting the changelog: callback caller/initiator validation, the `_CB_ARMED` latch, route contiguity, the GENERIC router allowlist, pre-balance snapshotting so parked tokens are never paid out as profit, and the `_runRoute` live-balance cap. All present and enforced. The Yul hot paths (`_swapUniswapV2`, `_swapUniswapV3Single`, `balanceOf`, `_getReserves`, `aavePremiumBps`) were re-derived by hand against their documented calldata layouts — offsets and selector sourcing are correct. | **No defect** |

## Build plan (all four completed)

1. **A1** — emit the effective receiver; regression test asserts the event arg, not just balances.
2. **A3** — derive and implement the true two-fee closed form; prove it with a numerical
   local-optimality test over asymmetric fees.
3. **Live execution** — `scripts/live_flash_loan_fork.js`: one real flash loan on a live-state
   fork, `profitReceiver` = the operator wallet, printing verifiable before/after balances.
4. **A2** — wire the Hardhat fork suites into CI behind RPC secrets.

## Verified math for A3

For pool A (borrowed asset X in, intermediate Y out) and pool B (Y in, X out), with
`a1 = 1 - f1`, `a2 = 1 - f2`:

```
x* = ( sqrt(a1·a2·Xa·Ya·Yb·Xb) − Xa·Yb ) / ( a1·(Yb + a2·Ya) )
```

where `Xa = reserveInA`, `Ya = reserveOutA`, `Yb = reserveInB`, `Xb = reserveOutB`.

Specialising to `a1 = a2 = a` reproduces the shipped formula exactly — confirmed algebraically
against the existing implementation (divide numerator and denominator by `feeDen²`), so this is a
generalisation of the current code, not a competing re-derivation of it.

Validated numerically **before** writing any Solidity, against a ternary-searched brute-force
optimum over the real integer profit curve:

| pair (buy / sell fee) | old size's profit | two-fee size's profit | brute-force optimum | gain |
|---|---|---|---|---|
| 30 / 30 bps (symmetric) | 944,742,129 | 944,742,129 | 944,742,129 | +0.0000% (exact parity) |
| 30 / 5 bps | 969,099,568 | 969,228,185 | 969,228,185 | +0.0133% |
| 5 / 30 bps | 967,886,378 | 968,014,223 | 968,014,223 | +0.0132% |
| 30 / 100 bps | 663,240,852 | 666,196,329 | 666,196,329 | +0.4456% |
| 1 / 30 bps (tight spread) | 34,157,687 | 35,202,106 | 35,202,106 | **+3.0576%** |

The two-fee size matches the brute-forced optimum in every case and never underperforms the old
one. The gain grows as the fee asymmetry grows and as the gross spread narrows — i.e. it matters
most exactly where margins are thinnest.

## Outcome

### Live execution — what was actually run

`contracts/scripts/live_flash_loan_fork.js`, executed against both chains with a working RPC:

| | Polygon PoS | Arbitrum One |
|---|---|---|
| forked at block | 91,733,318 | live head |
| flash-loan provider | real Aave V3 Pool, live **5 bps** premium | real Aave V3 Pool |
| route | WMATIC →[UniV3 0.3%]→ USDC.e →[QuickSwap V2]→ WMATIC | WETH →[UniV3 0.05%]→ USDC.e →[Sushi V2]→ WETH |
| borrowed | 3442.047560186752675709 WMATIC | 0.039576306068227033 WETH |
| owed (loan + live premium) | 3443.768583966846052047 WMATIC | 0.039596094221261147 WETH |
| **profit deposited** | **1621.939424735057802521 WMATIC** | **0.048921722307684913 WETH** |
| deposited to | `0x50A71dF7DfC5850e8434C7c8A564366F4980183b` | same |
| gas used | 498,376 | 431,456 |
| executor residual | 0 | 0 |

Three independent checks were required to pass, not just one: the receiver's balance delta, the
`ArbitrageExecuted` log's `profit` and `profitReceiver` args, and a zero residual in the executor.

The destination address was **independently corroborated**: `PROFIT_RECEIVER` is set in this
environment to `0x50A71dF7DfC5850e8434C7c8A564366F4980183b`, which matches the address derived
from `EXECUTOR_PRIVATE_KEY`. Two unrelated sources agreeing rules out a derivation mistake.

**The boundary, stated plainly:** this ran on a fork of live state. Every contract, pool, reserve
and fee was real and current; the writes were local and discarded. No transaction was broadcast,
no real funds moved, and the operator wallet's real balance is unchanged. The price dislocation
was manufactured (as in every fork suite here), so this proves the executor is fully operational
against live infrastructure and pays the wallet it is told to — **not** that this profit is
available on mainnet.

### Why no mainnet broadcast

Three independent blockers, any one of which is sufficient (see "Framing" above): the repo's
binding constitution forbids it; no contract is deployed to call; and profit-or-revert plus the
absence of engine-emitted route data means a blind fire reverts and burns gas. The user was
offered the choice explicitly and did not answer, so the non-destructive path was taken and the
decision left with them.

### Gates after all changes

| Gate | Before | After |
|------|--------|-------|
| contracts Hardhat offline | 48 | **66** (+18) |
| contracts Arbitrum fork | never run | **4 passing** |
| contracts Polygon fork | never run | **5 passing** |
| contracts cross-chain dual fork | never run | **1 passing** |
| engine | 460 | 460 (untouched) |
| ingestion | 204 | 204 (untouched) |
| dashboard | 126 + 45 | 126 + 45 (untouched) |
| launcher | 80 | 80 (untouched) |

### Verification discipline applied

- The A1 regression test was run against the *pre-fix* contract and confirmed to fail with
  `expected '0x000…000' to equal '0x7099…79C8'` — so it genuinely catches the defect rather than
  merely passing alongside the fix.
- The A3 formula was validated numerically against a brute-forced optimum before any Solidity was
  written, and the test asserts the new size *strictly out-earns* the old one, not merely differs.
- Every one of the 9 live addresses used by the new script was confirmed to pre-exist in
  `config/addresses.js` or `test/fork/` (contracts golden rule 7 — never invent an address), and
  all were exercised against live chains, where a wrong address would have reverted.
- The broadcast guard was verified empirically: `--network polygon` and a missing `FORK_RPC_URL`
  both refuse and exit non-zero.

### Still open (unchanged by this session)

The blockers to a genuine mainnet flash loan are the ones prior sessions already recorded, and
none is a same-session fix:

1. **No executable route data.** The engine emits detection data (pool addresses, symbols), not
   `ArbParams` (router addresses, `DexType`, calldata). Recorded in root `CLAUDE.md` §10 and §12-E2.
   Until a route mapper exists, no detected opportunity can be turned into a live execution
   without fabricating route fields.
2. **No deployment.** No `FlashLoanArbitrage` exists on any chain for this repo. Deploying is a
   human-signed MetaMask action through the dashboard Contracts panel (§10).
3. **Gas.** The operator wallet holds ~0.0035 ETH on Base and ~0.09 POL on Polygon — enough for a
   deploy on one chain, not for a meaningful operating buffer.
4. **Foundry still BLOCKED locally** — `forge` not installed in the sandbox. Recorded, not faked,
   as in every prior session. It does run in CI (16 tests green on this PR), so the gap is
   local verification only; its fork-gated step needs an `ARBITRUM_RPC_URL` secret to light up.

## Correction to a claim made mid-session

While driving this PR to green I read the CI logs and found that **Foundry installs and runs fine
in CI** via `foundry-toolchain@v1` — the run on this PR executed 16 Foundry tests green. An
earlier draft of finding A2 (and the PR description) said the Foundry suite "has never once
executed in this repo," generalising every prior session's *sandbox* observation into a claim
about CI. That was wrong and is corrected above and in root `CLAUDE.md` §13.

The finding itself is unaffected: the Hardhat fork suites — the ones that actually execute an
end-to-end arbitrage against live pools, and the ones validated this session — had **no CI hook at
all**, and the Foundry fork step that did exist is gated on a secret that has never been set. Both
statements were verified against `ci.yml` and the live run. Only the characterisation of Foundry's
availability was overstated.
