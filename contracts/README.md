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
| `test/*.test.js` (offline) | Full mechanics on mock tokens/pools/providers: 2-hop & 3-hop arbs via Aave and Balancer, min-profit revert, access control, pause, griefer-callback rejection, GENERIC-router allowlisting, optimal-sizing math | **29 passing** |
| `test/fork/ArbitrumFork.test.js` (**live Arbitrum fork**) | **Real** atomic cross-DEX flash-loan arbs against **live** Uniswap V3 + SushiSwap V2, borrowing from **both Balancer V2 and Aave V3**, plus a live Aave premium read and an atomic revert when no arb exists | **4 passing** |

The live-fork test manufactures a price dislocation and then **captures it
atomically**, e.g. a real run banked **`0.957 WETH` profit on a `0.82 WETH`
flash loan** across three live protocols on an Arbitrum One fork — proving the
borrow → cross-DEX multi-hop → repay → profit path is fully operational on-chain.

```
FlashLoanArbitrage — Arbitrum mainnet fork (live contracts)
  ✔ reads the live Aave V3 flash-loan premium
  ✔ reverts atomically when no real arbitrage exists (prices aligned)
    captured profit: 0.957087037393423376 WETH on a 0.818659951973042504 WETH loan
  ✔ executes a real atomic cross-DEX flash-loan arb and banks the profit
```

---

## Quickstart

```bash
# 1. Install
npm install

# 2. Compile (downloads solc 0.8.20 on first run)
npm run compile

# 3. Offline unit tests (no RPC needed) — 24 tests
npm test

# 4. Live mainnet-fork tests against real Arbitrum contracts
FORK_RPC_URL=https://arb1.arbitrum.io/rpc npm run test:fork
```

Foundry is fully supported too (same sources):

```bash
curl -L https://foundry.paradigm.xyz | bash && foundryup
forge install foundry-rs/forge-std OpenZeppelin/openzeppelin-contracts@v5.0.2
forge build
forge test --match-path 'test/foundry/*' --fork-url "$ARBITRUM_RPC_URL" -vvv
```

Deploy:

```bash
cp ../.env.example ../.env     # master .env at the repo root; add PRIVATE_KEY (deploy-only)
npx hardhat run scripts/deploy.js --network arbitrum
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
    DexRouter.sol                 # per-hop executor (V2/V3/Curve/GENERIC) + Yul helpers
    OptimalArbitrage.sol          # closed-form optimal sizing + Yul sqrt
  interfaces/                     # Aave, Balancer, DEX, bridge interfaces
  mocks/                          # test doubles (offline suite)
test/
  *.test.js                       # offline Hardhat suites (24 tests)
  fork/ArbitrumFork.test.js       # live mainnet-fork suite (3 tests)
  foundry/*.t.sol                 # Foundry mirror of the fork suite
script/Deploy.s.sol               # Foundry deploy script
scripts/deploy.js                 # Hardhat deploy script
config/addresses.js               # per-chain address book (VERIFY before use)
addresses/addresses.json          # same, language-agnostic
integration/
  abi/*.abi.json                  # ABIs for any-language integration
  examples/bot.js, bot.py         # reference bots (ethers + web3.py)
docs/                             # ARCHITECTURE, DEPLOYMENT, INTEGRATION
```

---

## Limitations

Stated plainly, because a senior engineer should:

1. **Cross-chain arbitrage cannot be atomic, and cannot use a flash loan.** A
   flash loan is borrowed and repaid inside one transaction on one chain; a
   transaction cannot span chains. Any "atomic cross-chain flash-loan arbitrage"
   claim is a category error. The included `CrossChainArbitrageExecutor` uses
   the realistic **inventory-based** model (two txs on two chains, capital in
   flight between them) and documents the risks in-source.
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
