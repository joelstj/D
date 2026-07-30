# L2 On-Chain Flash-Loan Arbitrage Engine

A production-oriented, gas-optimized flash-loan **arbitrage engine** for EVM
Layer-2 networks — **Optimism, Base, Arbitrum One, Polygon** (and, once you
verify their provider addresses, **Unichain** and **Ink**).

One address-agnostic contract executes **atomic, single-transaction**
arbitrage:

- **2-hop** (buy on DEX A, sell on DEX B)
- **3-hop / N-hop** triangular routes
- **cross-DEX** routes that mix Uniswap V2, Uniswap V3, Curve and any other
  venue in a single trade
- **dynamic loan sizing** that scales the borrow to live pool liquidity via a
  closed-form optimal-size solver
- borrowing from **Aave V3** or **Balancer V2** (0-fee) per call

Cross-chain arbitrage is addressed honestly with a separate, **inventory-based
(non-atomic)** executor — because a flash loan, by construction, cannot span two
chains (see [Limitations](#limitations)).

> ⚠️ **This is infrastructure, not a money printer.** The contract guarantees
> that a trade is *atomic and profit-checked* — it reverts (costing only gas) if
> a route would not clear your `minProfit`. It does **not** find opportunities;
> your off-chain bot does. Profitability depends entirely on live market
> conditions. Read [Limitations](#limitations) before deploying real funds.

---

## Verified in this repo

Compiled with **solc 0.8.20** (zero warnings) and tested with Hardhat:

| Suite | What it proves | Result |
| --- | --- | --- |
| `test/*.test.js` (offline) | Full mechanics on mock tokens/pools/providers: 2-hop & 3-hop arbs via Aave and Balancer, min-profit revert, access control, pause, griefer-callback rejection, GENERIC-router allowlisting, optimal-sizing math, and Yul-encoded DEX call differential tests | **37 passing** |
| `test/fork/ArbitrumFork.test.js` (**live Arbitrum fork**) | **Real** atomic cross-DEX flash-loan arbs against **live** Uniswap V3 + SushiSwap V2, borrowing from **both Balancer V2 and Aave V3**, plus a live Aave premium read and an atomic revert when no arb exists | **4 passing** |
| `test/fork/PolygonFork.test.js` (**live Polygon PoS fork**) | The same atomic borrow → cross-DEX → repay → profit path against **live** Uniswap V3 + QuickSwap V2 on Polygon | **5 passing** |
| `test/fork/CrossChainDualFork.test.js` (**live dual fork: Polygon + Arbitrum**) | `CrossChainArbitrageExecutor`'s inventory-based source leg (real swap on a live Polygon fork) and destination leg (real swap on a live Arbitrum fork), run back-to-back in one test against genuinely independent live chain state | **1 passing** |

The live-fork tests manufacture a price dislocation and then **capture it
atomically**, e.g. real runs banked **`0.94 WETH` profit on a `0.80 WETH`
flash loan** on Arbitrum and **`2739 WMATIC` profit on a `3593 WMATIC` loan**
on Polygon — proving the borrow → cross-DEX multi-hop → repay → profit path is
fully operational on-chain on both chains.

```
FlashLoanArbitrage — Arbitrum mainnet fork (live contracts)
  ✔ reads the live Aave V3 flash-loan premium
  ✔ reverts atomically when no real arbitrage exists (prices aligned)
    captured profit: 0.941577823501729047 WETH on a 0.799339691768452745 WETH loan
  ✔ executes a real atomic cross-DEX flash-loan arb and banks the profit

FlashLoanArbitrage — Polygon PoS mainnet fork (live contracts)
  ✔ finds a live QuickSwap WMATIC/USDC.e pair with real reserves
  ✔ reads the live Aave V3 flash-loan premium
  ✔ reverts atomically when no real arbitrage exists (prices aligned)
    captured profit: 2739.100495288613310372 WMATIC on a 3592.593085775975007738 WMATIC loan
  ✔ executes a real atomic cross-DEX flash-loan arb and banks the profit

CrossChainArbitrageExecutor — dual real fork (Polygon source -> Arbitrum destination)
  Polygon source leg: bridged 0.037556699704030696 WETH to the (mock) bridge adapter
  Arbitrum destination leg: settled into 70.888678 USDC.e
  ✔ executes the source leg on a real Polygon fork, then the destination leg on a real Arbitrum fork
```

**On "cross-chain flash loan execution":** read this before running it. A
flash loan is borrowed and repaid inside one transaction on one chain — a
transaction cannot span two chains, so no atomic *cross-chain* flash loan
exists on any EVM chain today (see [Limitations](#limitations) and
[`docs/specs/10-cross-chain.md`](docs/specs/10-cross-chain.md)). What the
dual-fork suite above proves is the real, honest model:
`CrossChainArbitrageExecutor`'s inventory-based two-transaction flow, with
each leg executed for real against independently-live state on its own
chain. The only simulated step, in both the Hardhat and Foundry versions, is
the bridge/relayer itself — no real relayer can act on an ephemeral local
fork — which is exactly what this repo's own cross-chain spec calls for
("messaging is mocked deterministically in tests").

---

## Quickstart

```bash
# 1. Install
npm install

# 2. Compile (downloads solc 0.8.20 on first run)
npm run compile

# 3. Offline unit tests (no RPC needed) — 33 tests
npm test

# 4. Live mainnet-fork tests against real contracts
FORK_RPC_URL=https://arb1.arbitrum.io/rpc npm run test:fork            # Arbitrum
FORK_RPC_URL=https://polygon-rpc.com npm run test:fork:polygon         # Polygon

# 5. Live dual-fork cross-chain execution test (both chains, one test run)
POLYGON_RPC_URL=https://polygon-rpc.com \
ARBITRUM_RPC_URL=https://arb1.arbitrum.io/rpc \
  npm run test:fork:crosschain
```

> A handful of free public RPCs (e.g. `polygon-rpc.com`) rate-limit or block
> the bulk archival-style calls a mainnet fork makes. If a fork test fails
> with a proxy/RPC error rather than a contract error, try another public
> endpoint (`https://polygon.gateway.tenderly.co` and
> `https://polygon-bor-rpc.publicnode.com` both work) or your own
> Alchemy/Infura/QuickNode key — this is an RPC-provider limitation, not a
> contract issue.

Foundry is fully supported too (same sources):

```bash
curl -L https://foundry.paradigm.xyz | bash && foundryup
forge install foundry-rs/forge-std OpenZeppelin/openzeppelin-contracts@v5.0.2
forge build
forge test --match-path 'test/foundry/*' --fork-url "$ARBITRUM_RPC_URL" -vvv
forge test --match-path 'test/foundry/PolygonFork.t.sol' --fork-url "$POLYGON_RPC_URL" -vvv
# Cross-chain: creates both forks itself, so no single --fork-url here.
POLYGON_RPC_URL=... ARBITRUM_RPC_URL=... \
  forge test --match-path 'test/foundry/CrossChainDualFork.t.sol' -vvv
```

Deploy (both `FlashLoanArbitrage` and `CrossChainArbitrageExecutor` — the
latter needs one sibling deployment per chain you bridge between, so run this
once per chain; pass `SKIP_CROSSCHAIN=1` to deploy only the flash-loan engine):

```bash
cp ../.env.example ../.env     # master .env at the repo root; add PRIVATE_KEY (deploy-only)
npx hardhat run scripts/deploy.js --network arbitrum
npx hardhat run scripts/deploy.js --network polygon
```

Deploy config (`PRIVATE_KEY`, the `*_RPC_URL` endpoints, `ETHERSCAN_API_KEY`)
lives in the repo-root master `.env`, in its clearly-fenced *Contracts* section —
these are read **only** for this human-gated deploy/verify step, never by the
running detection stack. Prefer to keep the deploy key isolated? Put it in a local
`contracts/.env` instead; it overrides the master. Never commit a real key.

See **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)** for the full per-chain guide and
**[docs/INTEGRATION.md](docs/INTEGRATION.md)** for the plug-and-play bot API.

---

## How the four modes map to one contract

Everything is expressed as an ordered `SwapStep[]` route where **each hop swaps
the entire running balance forward**. The route must start and end in the
borrowed `asset`.

| Mode | Route |
| --- | --- |
| 2-hop | `[asset→X on DEX_A, X→asset on DEX_B]` |
| 3-hop (triangular) | `[asset→X, X→Y, Y→asset]` (any mix of DEXs) |
| Cross-DEX | any hop can target a different venue — Uni V2, Uni V3, Curve, or a raw `GENERIC` call |
| Cross-chain | **separate** inventory-based executor, two txs on two chains (non-atomic) |

Because each hop consumes the full running balance, **the same route works at
any loan size** — that is what makes dynamic sizing possible.

### Dynamic loan sizing

`quoteOptimalTwoHopV2(pairBuy, pairSell, tokenBorrow, feeBpsBuy, feeBpsSell)`
returns the **profit-maximising** borrow amount for the classic two-pool case,
derived in closed form from live reserves (see
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#optimal-loan-sizing)). It scales up
when liquidity is deep and down when it's thin, so you neither leave profit on
the table nor blow through slippage. Your bot can call it (or use its own
sizing) and pass the result as `amount`. Whatever size you choose, the on-chain
`minProfit` guard is the ultimate backstop: **an unprofitable size simply
reverts.**

---

## Security model

- **OpenZeppelin** `AccessControl`, `ReentrancyGuard`, `Pausable`, `SafeERC20`.
- Only `EXECUTOR_ROLE` can start an arbitrage; only `GUARDIAN_ROLE` can pause,
  sweep funds, or allowlist a router for `DexType.GENERIC` hops.
- `GENERIC` hops (the raw-calldata escape hatch for exotic venues) may only
  target a router `GUARDIAN_ROLE` has explicitly allowlisted via
  `setGenericRouterAllowed` (deny-all by default) — otherwise a compromised
  `EXECUTOR_ROLE` key could direct a `GENERIC` step's arbitrary calldata to
  move the contract's entire balance of any held token, not just the current
  hop's amount. The typed dex types (V2/V3/Curve) don't need this: their call
  shape is fixed to a well-known selector with the output recipient hardcoded
  to `address(this)`.
- **Checks-effects-interactions** throughout; `nonReentrant` on the entry point.
- Both flash callbacks verify the caller **is** the expected provider **and**
  that this contract itself armed the loan — blocking griefers who try to invoke
  the callback with an unsolicited flash loan (covered by tests).
- **Custom errors** everywhere (no revert strings) for gas and precise
  simulation feedback.
- Historical PnL lives in **events**, not storage.
- The engine is designed to **hold no idle funds**; profit is forwarded every
  trade and a guardian `rescueTokens`/`rescueETH` can sweep anything stuck.
- **Yul is scoped to the hot path, not everywhere.** `DexRouter.balanceOf`,
  `FlashLoanArbitrage.aavePremiumBps`/`_getReserves`/`_token0`/`_token1`, and
  `OptimalArbitrage.sqrt`/`getAmountOut` are raw assembly (single-word
  staticcall reads and pure integer math); `DexRouter`'s `UNISWAP_V2` /
  `UNISWAP_V3_SINGLE` swap calls are hand-encoded in assembly too (fixed-shape
  calls this engine always makes — a static 2-element path, a fully static
  params struct), each proven against a plain-Solidity/JS reference in
  `test/DexRouter.test.js` and `test/OptimalArbitrage.test.js`, and against
  live routers on both fork suites. Approval/transfer logic
  (`SafeERC20.forceApprove`/`safeTransfer`) and admin functions (pause, role
  grants, rescue) are deliberately left in plain Solidity — OpenZeppelin
  already handles non-standard-token approval semantics correctly, and
  rewriting that in assembly for a marginal gas win is exactly the kind of
  risk not worth taking for code this security-critical.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full threat model.

---

## Repository layout

```
contracts/
  FlashLoanArbitrage.sol          # main atomic engine (Aave V3 + Balancer V2)
  crosschain/
    CrossChainArbitrageExecutor.sol  # inventory-based, non-atomic cross-chain
  libraries/
    ArbTypes.sol                  # shared structs/enums (ABI surface)
    DexRouter.sol                 # per-hop executor (V2/V3/Curve/GENERIC) + Yul-encoded V2/V3Single calls
    OptimalArbitrage.sol          # closed-form optimal sizing + Yul sqrt/getAmountOut
  interfaces/                     # Aave, Balancer, DEX, bridge interfaces
  mocks/                          # test doubles (offline suite; incl. MockUniV3Router, DexRouterHarness)
test/
  *.test.js                       # offline Hardhat suites (33 tests)
  fork/ArbitrumFork.test.js       # live Arbitrum mainnet-fork suite (4 tests)
  fork/PolygonFork.test.js        # live Polygon mainnet-fork suite (5 tests)
  fork/CrossChainDualFork.test.js # live dual-fork cross-chain execution proof (1 test)
  foundry/*.t.sol                 # Foundry mirrors of the fork suites (written; see docs/DEPLOYMENT.md)
script/Deploy.s.sol               # Foundry deploy script (FlashLoanArbitrage + CrossChainArbitrageExecutor)
scripts/deploy.js                 # Hardhat deploy script (same two contracts)
config/addresses.js               # per-chain address book (VERIFY before use)
addresses/addresses.json          # same, language-agnostic
integration/
  abi/*.abi.json                  # ABIs for any-language integration
  examples/bot.js, bot.py         # reference bots (ethers + web3.py)
docs/                             # ARCHITECTURE, DEPLOYMENT, INTEGRATION, notes-cross-chain-flash-loans.md
```

> **Two generations, one component.** `ralph/` and `src/` are a separate,
> in-progress rewrite of this same component (a from-scratch, Yul-first,
> route-codec architecture driven by an autonomous "Ralph" loop — see
> `contracts/CLAUDE.md`), still at skeleton stage and out of scope for
> Polygon. This README, and everything above, describes the tree actually
> being extended and verified (`contracts/`, `test/`, `script/`, `scripts/`)
> — the one Hardhat/CI already build and test today.

---

## Limitations

Stated plainly, because a senior engineer should:

1. **Cross-chain arbitrage cannot be atomic, and cannot use a flash loan.** A
   flash loan is borrowed and repaid inside one transaction on one chain; a
   transaction cannot span chains. Any "atomic cross-chain flash-loan arbitrage"
   claim is a category error. The included `CrossChainArbitrageExecutor` uses
   the realistic **inventory-based** model (two txs on two chains, capital in
   flight between them) and documents the risks in-source. `test/fork/
   CrossChainDualFork.test.js` proves both legs for real against two
   independently-live forked chains; only the bridge/relayer delivery between
   them is simulated, since no real relayer can act on an ephemeral local fork
   — see that file's header and [Verified in this repo](#verified-in-this-repo).
   No hedging, exposure caps, or bridge-failure/refund handling are
   implemented here — those are real, separate engineering work (inventory
   drift accounting, a real bridge adapter, a funded treasury) before any
   capital should move through this path; see `docs/specs/10-cross-chain.md`.
2. **Profitability is not guaranteed.** The contract enforces atomic
   profit-or-revert; it does not create opportunities. Efficient markets, gas,
   MEV competition and latency all bear on real PnL.
3. **Fork tests must run under the Cancun EVM spec.** Aave V3.3's flash-loan
   guard uses EIP-1153 transient storage, so `flashLoanSimple` reverts with
   `NotActivated` under older specs; both toolchains pin Cancun so the live-fork
   suite borrows from **Aave V3 *and* Balancer V2**. See
   [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md#a-note-on-aave-v3-on-forks--the-evm-hardfork-matters).
4. **Fee-on-transfer and rebasing tokens are unsupported** — the balance-delta
   accounting would misprice them.
5. **Addresses in `config/addresses.js` must be verified** against official docs
   before mainnet use. Unichain/Ink provider addresses are left `null` pending
   verification.
6. **This is not audited.** Get a professional audit before deploying
   significant capital.

---

## Disclaimer

Provided as-is, for lawful arbitrage and research. You are responsible for
compliance with all applicable laws and for the security of your keys and funds.
Nothing here is financial advice.

License: MIT.
