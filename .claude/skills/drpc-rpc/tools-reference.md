# DRPC MCP Tools Reference

All EVM tools use standard Ethereum JSON-RPC names and accept `{ network, params }`.

Need to force a client implementation such as `reth`, `geth`, or `erigon`? MCP tools usually do not expose routing selectors; use the direct JSON-RPC endpoint in [client-selection.md](client-selection.md) for that specific call.

## Discovery Tools

### list_networks
- **Arguments:** none
- **Returns:** array of `{ name, chainId, nativeCurrency }`

### list_methods
- **Arguments:** `{ network }`
- **Returns:** array of RPC method names available for that network

### get_network_info
- **Arguments:** `{ network }`
- **Returns:** chain ID, protocol, native currency, block explorers, expected block time

## Balance & Account

### eth_getBalance
- **Arguments:** `{ network, params: [address, blockTag] }`
- **Returns:** hex string (wei)
- **Convert:** divide by 10^18 for ETH/MATIC/BNB/AVAX

### eth_getTransactionCount
- **Arguments:** `{ network, params: [address, blockTag] }`
- **Returns:** hex nonce

## Blocks

### eth_getBlockByNumber
- **Arguments:** `{ network, params: [blockNumberOrTag, fullTransactions] }`
- blockNumberOrTag: `"latest"`, `"earliest"`, `"pending"`, `"safe"`, `"finalized"`, or hex
- fullTransactions: `true` for full tx objects, `false` for hashes only

### eth_getBlockByHash
- **Arguments:** `{ network, params: [blockHash, fullTransactions] }`

## Transactions

### eth_getTransactionByHash
- **Arguments:** `{ network, params: [txHash] }`

### eth_getTransactionReceipt
- **Arguments:** `{ network, params: [txHash] }`
- **Key fields:** `status` (`"0x1"` = success, `"0x0"` = reverted), `gasUsed`, `logs`

## Contracts

### eth_call
- **Arguments:** `{ network, params: [{ to, data, from?, value? }, blockTag] }`
- **Common selectors:**
  - `0x70a08231` + padded address = `balanceOf(address)` (ERC-20)
  - `0x95d89b41` = `symbol()`
  - `0x313ce567` = `decimals()`
  - `0x18160ddd` = `totalSupply()`
  - `0x06fdde03` = `name()`

### eth_getCode
- **Arguments:** `{ network, params: [address, blockTag] }`
- **Returns:** hex bytecode (`"0x"` if not a contract)

### eth_estimateGas
- **Arguments:** `{ network, params: [{ from?, to, data?, value? }] }`
- **Returns:** hex gas estimate

## Logs

### eth_getLogs
- **Arguments:** `{ network, params: [{ fromBlock, toBlock, address?, topics? }] }`
- Keep block range narrow (max 1000 blocks)
- Topics support null wildcards and OR arrays

## Gas

### eth_gasPrice
- **Arguments:** `{ network }`
- **Returns:** hex string (wei). Divide by 10^9 for gwei.

## Generic

### rpc_call
- **Arguments:** `{ network, method, params }`
- Any JSON-RPC method on any network. Use for non-EVM chains (Solana, Bitcoin, NEAR, Cosmos) or uncommon EVM methods.

### rpc_batch
- **Arguments:** `{ network, requests: [{ method, params, id? }] }`
- Batch multiple calls in one request
- Free tier: max 3 calls per batch
