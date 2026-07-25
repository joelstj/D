# DeFi Basics — Universal Recipes

Generic DeFi recipes that work on any EVM network using standard interfaces.

## ERC-20 Selectors

```
balanceOf(address)            → 0x70a08231
symbol()                      → 0x95d89b41
decimals()                    → 0x313ce567
name()                        → 0x06fdde03
totalSupply()                 → 0x18160ddd
allowance(address,address)    → 0xdd62ed3e
```

Address padding: remove `0x`, left-pad to 64 hex chars with zeros.
Example: `0x742d...048C` → `000000000000000000000000742d35Cc6635C0532925a3b8D35399Cc048C50E9`

## Full ERC-20 Portfolio

Check balances of major tokens for an address.

**Steps:**
1. Pick top tokens for the network (see token list below)
2. For each token, batch three `eth_call`s: `balanceOf`, `symbol`, `decimals`
3. Use `rpc_batch` to execute all calls in one request
4. Convert raw balance: `balance / 10^decimals`
5. Filter out zero balances, present as table

**Token call format:**
```json
{"method": "eth_call", "params": [{"to": "TOKEN_ADDR", "data": "0x70a08231000000000000000000000000USER_ADDR_NO_0x"}, "latest"]}
```

**Common tokens by network:**

Ethereum:
- USDT: `0xdAC17F958D2ee523a2206206994597C13D831ec7` (6 decimals)
- USDC: `0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48` (6 decimals)
- WETH: `0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2` (18 decimals)
- DAI: `0x6B175474E89094C44Da98b954EedeAC495271d0F` (18 decimals)
- WBTC: `0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599` (8 decimals)
- stETH: `0xae7ab96520DE3A18E5e111B5EaAb095312D7fE84` (18 decimals)
- LINK: `0x514910771AF9Ca656af840dff83E8264EcF986CA` (18 decimals)
- UNI: `0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984` (18 decimals)

Arbitrum:
- USDT: `0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9` (6)
- USDC: `0xaf88d065e77c8cC2239327C5EDb3A432268e5831` (6)
- WETH: `0x82aF49447D8a07e3bd95BD0d56f35241523fBab1` (18)
- ARB: `0x912CE59144191C1204E64559FE8253a0e49E6548` (18)

Base:
- USDC: `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913` (6)
- WETH: `0x4200000000000000000000000000000000000006` (18)
- cbETH: `0x2Ae3F1Ec7F1F5012CFEab0185bfc7aa3cf0DEc22` (18)

Optimism:
- USDC: `0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85` (6)
- WETH: `0x4200000000000000000000000000000000000006` (18)
- OP: `0x4200000000000000000000000000000000000042` (18)

Polygon:
- USDT: `0xc2132D05D31c914a87C6611C10748AEb04B58e8F` (6)
- USDC: `0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359` (6)
- WMATIC: `0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270` (18)
- WETH: `0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619` (18)

## Token Approval Audit

Find and display all active token approvals for an address.

**Steps:**
1. Query `Approval` events for the user as owner:
   ```json
   {"method": "eth_getLogs", "params": [{"fromBlock": "0x0", "toBlock": "latest", "topics": ["0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925", "0x000000000000000000000000USER_ADDR_NO_0x"]}]}
   ```
   Note: this can be slow on large ranges. Start with recent blocks (last 100k).
2. Extract unique (token, spender) pairs from logs
3. For each pair, call `allowance(owner, spender)`:
   ```
   data: 0xdd62ed3e + padded_owner + padded_spender
   ```
4. Filter non-zero allowances
5. Get token symbol/decimals for display
6. Present: token, spender address, approved amount (unlimited if > 2^200)

**Unlimited approval:** If allowance is `0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff` or very large, flag as "unlimited".

## LP Token Detection

Determine if a token is a Uniswap v2-style LP pair.

**Steps:**
1. Call `token0()` on the address: `data: 0x0dfe1681`
2. If it reverts → not an LP token
3. If it returns an address → likely LP. Also call:
   - `token1()`: `0xd21220a7`
   - `getReserves()`: `0x0902f1ac` → returns (reserve0, reserve1, blockTimestampLast)
   - `totalSupply()`: `0x18160ddd`
4. Get symbol/decimals of token0 and token1
5. Calculate user's share: `userBalance / totalSupply * reserves`

## Wallet Net Worth Summary

Combine native + ERC-20 balances into one view.

**Steps:**
1. Call `eth_getBalance` for native token
2. Batch `eth_call` for top tokens on the network (see token list above)
3. Filter non-zero balances
4. Present as sorted table with native token first

For multi-chain net worth, run this on each network in parallel — see [recipes-crosschain.md](recipes-crosschain.md).

## Token Transfer History

Get recent token transfers for an address.

**Steps:**
1. Query `Transfer` events where user is sender OR receiver:
   - As sender: `topics: ["0xddf252ad...", "0x000...USER_ADDR"]`
   - As receiver: `topics: ["0xddf252ad...", null, "0x000...USER_ADDR"]`
2. Use recent block range (last 10k blocks)
3. Decode each log: `from` = topic[1], `to` = topic[2], `value` = data
4. Get token symbol/decimals
5. Present as chronological list: direction (IN/OUT), token, amount, counterparty, block

**Transfer event topic:** `0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef`

## Staking Detection

Check if an address holds liquid staking tokens.

**Steps:**
1. Batch `balanceOf` calls for known staking tokens:
   - Ethereum: stETH (`0xae7ab96520DE3A18E5e111B5EaAb095312D7fE84`), rETH (`0xae78736Cd615f374D3085123A210448E74Fc6393`), cbETH (`0xBe9895146f7AF43049ca1c1AE358B0541Ea49704`)
   - Arbitrum: wstETH (`0x5979D7b546E38E9Ab8041E4ee204D1Ad5523caFf`)
   - Optimism: wstETH (`0x1F32b1c2345538c0c6f582fCB022739c4A194Ebb`)
   - Base: cbETH (`0x2Ae3F1Ec7F1F5012CFEab0185bfc7aa3cf0DEc22`)
   - Polygon: stMATIC (`0x3A58a54C066FdC0f2D55FC9C89F0415C92eBf3C4`)
2. Filter non-zero balances
3. Present: token, balance, network
