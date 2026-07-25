# Simple Recipes (Single Network)

## Get Wallet Balance

1. Call `eth_getBalance` with `{ network, params: [address, "latest"] }`
2. Result is hex wei. Convert: divide by 10^18 for ETH/MATIC/BNB/AVAX
3. Present with token symbol and network name

## Check Transaction Status

1. Call `eth_getTransactionReceipt` with `{ network, params: [txHash] }`
2. Check `status`: `"0x1"` = success, `"0x0"` = reverted
3. Show `gasUsed`, `blockNumber`, logs count

## Read ERC-20 Token Balance

1. Get token decimals: `eth_call` with `data: "0x313ce567"` to token contract
2. Get balance: `eth_call` with `data: "0x70a08231" + padded address` to token contract
3. Divide raw balance by 10^decimals
4. Optionally get symbol: `eth_call` with `data: "0x95d89b41"`

Use `rpc_batch` to combine all three calls on the same network.

## Get Current Gas Price

1. Call `eth_gasPrice` with `{ network }`
2. Convert hex wei to gwei: divide by 10^9
3. Present with network name

## Get Latest Block

1. Call `eth_getBlockByNumber` with `{ network, params: ["latest", false] }`
2. Show block number (hex → decimal), timestamp, transaction count

## Look Up Transaction

1. Call `eth_getTransactionByHash` with `{ network, params: [txHash] }`
2. Show `from`, `to`, `value` (convert wei), `gas`, `input` data

## Query Event Logs

1. Call `eth_getLogs` with `{ network, params: [{ fromBlock, toBlock, address, topics }] }`
2. Keep block range narrow — max 1000 blocks per query
3. For wider ranges, paginate: split into chunks of 1000 blocks

## Check if Address is a Contract

1. Call `eth_getCode` with `{ network, params: [address, "latest"] }`
2. If result is `"0x"` → EOA (regular wallet)
3. If result has bytecode → smart contract

## Discover Networks

1. Call `list_networks` with no arguments
2. Returns all 200+ supported networks with chain IDs and native currencies
3. Use `get_network_info` for details about a specific network

## Tips

- Always use `list_networks` first if unsure which networks are available
- All addresses must be 0x-prefixed, 42-character hex strings
- Block tags: `"latest"`, `"earliest"`, `"pending"`, `"safe"`, `"finalized"` or hex number
- Use `rpc_batch` to combine multiple independent queries on the same network
- For non-EVM chains (Solana, Bitcoin, NEAR), use `rpc_call` with native methods
