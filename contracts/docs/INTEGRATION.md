# Integration Guide (plug-and-play, any language)

The contract is driven entirely through **one ABI function** and two plain
structs. Any language with an EVM ABI encoder (ethers, viem, web3.py, web3j,
ethers-rs, go-ethereum, Nethereum, …) can integrate it identically. ABIs live in
[`integration/abi/`](../integration/abi/); reference bots are in
[`integration/examples/`](../integration/examples/) (`bot.js`, `bot.py`).

## The one call

```solidity
function executeArbitrage(ArbParams calldata p) external;   // onlyRole(EXECUTOR_ROLE)
```

### `ArbParams`

| Field | Type | Meaning |
| --- | --- | --- |
| `provider` | `uint8` | `0` = Aave V3, `1` = Balancer V2 |
| `asset` | `address` | token to borrow & repay; route must start and end here |
| `amount` | `uint256` | loan size (see [sizing](#sizing)) |
| `minProfit` | `uint256` | revert unless net profit in `asset` ≥ this |
| `profitReceiver` | `address` | where profit is sent (`0` ⇒ the tx sender / executor) |
| `deadline` | `uint256` | unix seconds; revert if `block.timestamp` exceeds it |
| `steps` | `SwapStep[]` | ordered hops (≥2) |

### `SwapStep`

| Field | Type | Used by | Meaning |
| --- | --- | --- | --- |
| `dexType` | `uint8` | all | `0` V2, `1` V3-single, `2` V3-multi, `3` Curve, `4` GENERIC |
| `router` | `address` | all | router/pool to approve and call |
| `tokenIn` | `address` | all | token spent on this hop |
| `tokenOut` | `address` | all | token received (used for accounting) |
| `poolFee` | `uint24` | V3-single | fee tier (e.g. `500`, `3000`) |
| `curveI` | `int128` | Curve | coin index of `tokenIn` |
| `curveJ` | `int128` | Curve | coin index of `tokenOut` |
| `minOut` | `uint256` | all | per-hop slippage floor (`0` = skip) |
| `data` | `bytes` | V3-multi / GENERIC | V3 encoded path, or raw calldata |
| `amountInOffset` | `uint256` | GENERIC | byte offset at which to patch the live input amount into `data` (`0` = none) |

> Populate only the fields relevant to `dexType`; leave the rest zero/empty to
> save calldata gas.

## The golden pattern: **simulate, then send**

`executeArbitrage` reverts (cheaply) whenever a route wouldn't clear
`minProfit`. Use that as a free, exact profitability oracle:

1. `eth_call` (`staticCall`) `executeArbitrage(p)`.
   - **Succeeds** → the arb is profitable right now → send the real tx.
   - **Reverts `InsufficientProfit(generated, required)`** → skip; the error
     tells you exactly how far short you were.
2. Send the transaction (ideally via a private mempool/bundle for MEV safety).

This makes the bot **stateless and language-agnostic**: no need to re-implement
AMM math off-chain to know if a trade clears — ask the contract.

```js
// ethers v6
try { await arb.executeArbitrage.staticCall(p); }
catch (e) { return; /* not profitable */ }
await (await arb.executeArbitrage(p)).wait();
```

## Sizing

- **Constant-product two-pool arbs:** call the on-chain solver
  ```solidity
  (uint256 amount, uint256 expectedProfit) =
      arb.quoteOptimalTwoHopV2(pairBuy, pairSell, tokenBorrow, feeBpsBuy, feeBpsSell);
  ```
  and pass `amount` as your loan size. It scales with live liquidity.
- **V3 / mixed / N-hop:** size with your own model, or **bisect** on the
  simulate call — increase `amount` while `staticCall` keeps succeeding with
  rising profit, back off when it stops. The engine executes any size safely.

## Building routes

| Goal | `steps` |
| --- | --- |
| 2-hop cross-DEX | `[stepA(asset→X), stepB(X→asset)]` where A and B are different routers |
| 3-hop triangular | `[asset→X, X→Y, Y→asset]` |
| Uni V3 multi-hop leg | one `UNISWAP_V3_MULTI` step with `data` = `abi.encodePacked(tokenIn, fee, mid, fee, tokenOut)` |
| Curve stable leg | `CURVE` step with `curveI`/`curveJ` set to the pool's coin indices |
| Exotic venue | `GENERIC` step: put the router's raw calldata in `data`; if the input amount isn't known until runtime, set `amountInOffset` to the 32-byte word where the amount goes and the engine patches the live balance in |

## Events

```solidity
event ArbitrageExecuted(
    address indexed asset, uint8 indexed provider, address indexed profitReceiver,
    uint256 amountBorrowed, uint256 amountOwed, uint256 profit, uint256 hops
);
```

Index this for PnL/analytics — historical data is intentionally kept in logs,
not contract storage.

## Language notes

- **Structs** encode as ordered tuples. In `bot.py` a `SwapStep` is the tuple
  `(dexType, router, tokenIn, tokenOut, poolFee, curveI, curveJ, minOut, data, amountInOffset)`.
- **Empty bytes** = `"0x"` (ethers) / `b""` (web3.py).
- The function is state-changing; from any language, do a `call`/`eth_call`
  first for simulation, then a signed transaction.
- Only an `EXECUTOR_ROLE` key can call it; grant the role to your bot's address
  after deployment.
