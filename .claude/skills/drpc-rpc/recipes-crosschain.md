# Cross-Chain Recipes (Multi-Network)

## Multi-Chain Wallet Balance

Check a wallet's native token balance across all major EVM networks.

**Steps:**
1. Call `list_networks` to get available networks
2. Filter to EVM networks (ethereum, arbitrum, optimism, base, polygon, bsc, avalanche, etc.)
3. Call `eth_getBalance` in parallel for each network with the target address
4. Convert each hex result from wei to human-readable (divide by 10^18)
5. Present as a sorted table

**Example output:**
```
| Network    | Balance       | Token |
|------------|---------------|-------|
| ethereum   | 1.234 ETH     | ETH   |
| base       | 2.345 ETH     | ETH   |
| arbitrum   | 0.567 ETH     | ETH   |
| optimism   | 0.089 ETH     | ETH   |
| polygon    | 150.23 MATIC  | MATIC |
| bsc        | 0.5 BNB       | BNB   |
```

Use `rpc_batch` per network if also checking ERC-20 token balances — batch `eth_call` for each token's `balanceOf(address)`.

## L2 Gas Price Comparison

Find the cheapest L2 network for transactions.

**Steps:**
1. Call `eth_gasPrice` in parallel on: base, arbitrum, optimism, polygon, zksync, linea, scroll, mantle
2. Convert each hex result from wei to gwei (divide by 10^9)
3. Sort ascending by gas price
4. Present as a table

**Example output:**
```
| Network    | Gas Price (gwei) |
|------------|------------------|
| base       | 0.005            |
| arbitrum   | 0.01             |
| optimism   | 0.02             |
| scroll     | 0.03             |
| mantle     | 0.05             |
| linea      | 0.1              |
| zksync     | 0.25             |
| polygon    | 30               |
```

## Bridge Transaction Tracking

Track a cross-chain bridge transfer by checking both source and destination chains.

**Steps:**
1. Get source chain receipt: call `eth_getTransactionReceipt` on source network
2. Confirm source TX is successful (`status: "0x1"`)
3. Extract bridge event logs from the receipt to determine expected destination
4. Poll destination chain: call `eth_getLogs` on destination network, filtering for bridge completion events
5. Report status of both sides

**Common bridge contracts:**
- Arbitrum: `0x8315177aB297bA92A06054cE80a67Ed4DBd7ed3a` (L1 bridge)
- Optimism: `0x99C9fc46f92E8a1c0deC1b1747d010903E884bE1` (L1 bridge)
- Base: same as Optimism (OP Stack)

## Tips

- Call different networks in parallel (separate tool calls) for speed
- Use `rpc_batch` when querying multiple data points on the same network
- Network names must match exactly — use `list_networks` to verify
- Gas prices fluctuate — results are a snapshot, not a guarantee
