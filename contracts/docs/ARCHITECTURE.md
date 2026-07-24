# Architecture

## 1. Execution model

The engine is a **generic route executor** wrapped in a flash loan. One entry
point, `executeArbitrage(ArbParams)`, does the following atomically:

```
executeArbitrage(p)                       [onlyExecutor, nonReentrant, whenNotPaused]
 ├─ validate: deadline, ≥2 hops, route starts & ends in p.asset, amount>0
 ├─ snapshot preBalance = balanceOf(asset)          ← so parked funds are never counted as profit
 ├─ arm the callback latch
 └─ borrow p.amount of p.asset from Aave V3 or Balancer V2
      └─ (flash callback)                            [caller must be the provider AND latch armed]
           ├─ _runRoute(steps, amount)               ← feed-forward: hop N's output funds hop N+1
           ├─ generated = balanceOf(asset) − preBalance
           ├─ require generated ≥ owed + minProfit   ← else revert InsufficientProfit(generated, required)
           ├─ transfer (generated − owed) profit → profitReceiver
           └─ repay owed (approve Aave / transfer to Balancer)
```

### Feed-forward routing

`_runRoute` seeds hop 0 with the borrowed `amount`, then feeds **each hop's
measured output** (balance delta of `tokenOut`) into the next hop as its input,
capping at the live balance for safety. Consequences:

- **Any loan size works with the same route** — nothing is pre-computed per
  size. This is what enables dynamic sizing.
- **N hops = N steps.** 2-hop, 3-hop, N-hop are just longer arrays.
- **Cross-DEX is free** — each `SwapStep` names its own `router` and `dexType`,
  so a route can mix Uniswap V2, Uniswap V3, Curve and raw `GENERIC` calls.
- Output is measured, never trusted from a router return value, so the engine is
  agnostic to per-venue return-data quirks.

### Supported hop types (`DexType`)

| Type | Call | Notes |
| --- | --- | --- |
| `UNISWAP_V2` | `swapExactTokensForTokens` | any UniV2-compatible router |
| `UNISWAP_V3_SINGLE` | `exactInputSingle` | SwapRouter02 struct (no deadline) |
| `UNISWAP_V3_MULTI` | `exactInput(path)` | multi-hop V3 in one call; `data` = encoded path |
| `CURVE` | `exchange(int128,int128,...)` | low-level call (tolerates void-returning pools) |
| `GENERIC` | arbitrary calldata | optional `amountInOffset` patches the live input amount into the calldata at that byte offset (Yul) — integrate any venue with no upgrade |

---

## 2. Optimal loan sizing

For two constant-product (UniswapV2-style) pools, the profit-maximising input
amount has a **closed form**. Let the borrowed asset be `X`, the intermediate
`Y`, and let:

- Buy pool **A**: reserves `(Xa, Ya)` — spend `X`, receive `Y`.
- Sell pool **B**: reserves `(Yb, Xb)` — spend `Y`, receive `X`.
- Fee multiplier `a = (1 − fee)`, e.g. `a = 0.997` for a 0.30% pool.

Constant-product output for input `dx` (fee on input):

```
out(dx, rIn, rOut) = a·dx·rOut / (rIn + a·dx)
```

Round-trip return in `X` for a borrow `dx`:

```
R(dx) = out( out(dx, Xa, Ya), Yb, Xb )
      = a²·dx·Ya·Xb / ( Xa·Yb + dx·(Yb + a·Ya)·a⁻¹ … )
```

Profit `P(dx) = R(dx) − dx`. Setting `dP/dx = 0` and solving (the algebra
collapses because `d R/d dx = a²·Xa·Ya·Yb·Xb / D²` with
`D = Xa·Yb + dx·(Yb + a·Ya)`):

```
             feeDen · (feeNum·k − feeDen·Xa·Yb)
    dx*  =  ─────────────────────────────────────
             feeNum · (feeDen·Yb + feeNum·Ya)

    where  k = √(Xa·Ya·Yb·Xb),  feeNum = 10000 − feeBps,  feeDen = 10000
```

A profitable arb exists iff `feeNum·k > feeDen·Xa·Yb`; otherwise `dx* = 0`.
This is implemented in
[`OptimalArbitrage.optimalV2Amount`](../contracts/libraries/OptimalArbitrage.sol),
using a **Yul integer square root** and an overflow-safe factoring of `k` as
`√(Xa·Ya)·√(Yb·Xb)`. `FlashLoanArbitrage.quoteOptimalTwoHopV2` wires it to live
pair reserves.

**Accuracy.** `k` uses an integer sqrt and, when the two pools carry different
fees, the size is a close approximation. It is an *advisory*: the atomic
`minProfit` check — not this estimate — is the guarantee. The offline test
`OptimalArbitrage — finds the profit-maximising size` confirms the closed form
beats a dense grid of alternative sizes.

**Uniswap V3 / concentrated liquidity.** The closed form is exact only for
constant-product pools. For V3 legs, size off-chain against the pool's tick
liquidity (or bisect using `eth_call` simulations of `executeArbitrage`) and
pass the result as `amount`; the engine executes any size safely.

---

## 3. Security & threat model

| Threat | Mitigation |
| --- | --- |
| Unauthorized execution | `executeArbitrage` is `onlyRole(EXECUTOR_ROLE)` |
| Reentrancy | `nonReentrant` entry point; CEI ordering; trusted-provider callbacks only |
| **Griefer-initiated flash loan** (attacker calls `provider.flashLoan(ourContract, …)`) | Callback requires `msg.sender == provider` **and** an internal *armed* latch set only by our own `executeArbitrage`; Aave path additionally checks `initiator == address(this)` — all covered by tests |
| Direct callback calls | `executeOperation` / `receiveFlashLoan` revert unless caller is the provider and the latch is armed |
| Unprofitable / sandwiched trade | atomic `minProfit` guard reverts the whole tx; optional per-hop `minOut` floors |
| Stuck or parked funds | `preBalance` snapshot preserves idle balances; guardian `rescueTokens`/`rescueETH` |
| Malicious/odd tokens | `SafeERC20` + `forceApprove`; fee-on-transfer/rebasing explicitly unsupported |
| Operator key compromise | roles are separable — run the bot on a hot `EXECUTOR_ROLE` key and hold `DEFAULT_ADMIN_ROLE`/`GUARDIAN_ROLE` on a multisig; `pause()` for incident response |

### Trust assumptions

- The `EXECUTOR_ROLE` key is trusted to submit sane routes. Because routes carry
  arbitrary router addresses and (for `GENERIC`) arbitrary calldata, a
  compromised executor could route funds *it borrowed* poorly — but it can never
  exceed a single atomic tx, cannot touch parked capital beyond the borrowed
  amount's round trip, and any net loss simply reverts on `minProfit`. Keep the
  executor key operationally secure; keep admin/guardian on a multisig.

---

## 4. Gas & optimization notes

- **`viaIR` + optimizer at 1,000,000 runs**. The **compile** target is `paris`
  (portable across all target L2s; no `PUSH0` reliance in our own bytecode).
  Mainnet-fork **tests** execute under the `cancun` spec — required to run live
  contracts that use post-Paris opcodes (e.g. Aave V3.3's EIP-1153 transient
  storage); see [`DEPLOYMENT.md`](DEPLOYMENT.md#a-note-on-aave-v3-on-forks--the-evm-hardfork-matters).
- **Custom errors** instead of revert strings.
- **Yul** for the hot-path `balanceOf` (single staticcall, scratch memory) and
  the integer `sqrt`.
- **Libraries are `internal`** (inlined — no delegatecall overhead).
- **Immutable** provider addresses; **no historical storage** (events only).
- `unchecked` loop increments.
- Approvals are per-hop and exact; a pre-approval helper can be added if you
  prefer to trade a standing allowance for per-trade gas.

---

## 5. Cross-chain (why it's separate)

A flash loan must be repaid in the **same transaction** on the **same chain**,
so it is structurally impossible to flash-borrow on chain A and repay from chain
B. `CrossChainArbitrageExecutor` therefore uses the only model that actually
works: **pre-positioned inventory** plus a bridge, executed as **two separate
transactions** (`executeSourceLeg` on chain A, `executeDestinationLeg` on chain
B once funds arrive). Capital is *in flight* between the legs and exposed to
price and bridge risk — mitigate with fast intent/solver bridges (e.g. Across),
inventory on both sides, and conservative sizing. The contract documents this
prominently in-source.
