# DeFi Protocols — Protocol-Specific Recipes

Advanced recipes for specific DeFi protocols with contract addresses and ABI selectors.

## Aave v3 — Lending Position

Check a user's supply, borrow, and health factor on Aave v3.

**Pool contract addresses:**
- Ethereum: `0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2`
- Arbitrum: `0x794a61358D6845594F94dc1DB02A252b5b4814aD`
- Optimism: `0x794a61358D6845594F94dc1DB02A252b5b4814aD`
- Base: `0xA238Dd80C259a72e81d7e4664a9801593F98d1c5`
- Polygon: `0x794a61358D6845594F94dc1DB02A252b5b4814aD`

**Steps:**
1. Call `getUserAccountData(address)` on the Pool contract:
   ```
   data: 0xbf92857c + padded_user_address
   ```
2. Decode return (6 uint256 values):
   - [0] `totalCollateralBase` — total collateral in base currency (USD, 8 decimals)
   - [1] `totalDebtBase` — total debt in base currency (USD, 8 decimals)
   - [2] `availableBorrowsBase` — remaining borrow capacity (USD, 8 decimals)
   - [3] `currentLiquidationThreshold` — weighted avg threshold (basis points, /10000)
   - [4] `ltv` — weighted avg loan-to-value (basis points, /10000)
   - [5] `healthFactor` — health factor (18 decimals, <1.0 = liquidatable)

3. Convert:
   - USD values: divide by 10^8
   - Health factor: divide by 10^18
   - LTV/threshold: divide by 10000

**Example output:**
```
Aave v3 Position (Ethereum):
  Collateral:   $12,450.00
  Debt:         $5,200.00
  Available:    $3,100.00
  Health Factor: 1.85 (safe)
  LTV:          65%
```

**Health factor interpretation:**
- `> 2.0` — very safe
- `1.5 - 2.0` — safe
- `1.0 - 1.5` — risky, consider repaying
- `< 1.0` — liquidatable

## Aave v3 — Reserve Details

Get APY and utilization for a specific asset on Aave.

**Steps:**
1. Call `getReserveData(address asset)` on the Pool contract:
   ```
   data: 0x35ea6a75 + padded_asset_address
   ```
2. The return is a complex struct. Key fields (by offset in 256-bit words):
   - Word 3 (offset 0x60): `currentLiquidityRate` — supply APY (27 decimals, RAY)
   - Word 4 (offset 0x80): `currentVariableBorrowRate` — borrow APY (27 decimals, RAY)
3. Convert RAY to percentage: `rate / 10^27 * 100`

**Example:**
```
USDC on Aave v3 (Ethereum):
  Supply APY:  3.2%
  Borrow APY:  5.1%
```

## Uniswap v3 — LP Positions

Find and decode Uniswap v3 liquidity positions for an address.

**NonfungiblePositionManager addresses:**
- Ethereum / Arbitrum / Optimism / Polygon: `0xC36442b4a4522E871399CD717aBDD847Ab11FE88`
- Base: `0x03a520b32C04BF3bEEf7BEb72E919cf822Ed34f1`

**Steps:**
1. Get number of positions: `balanceOf(address)` on the position manager:
   ```
   data: 0x70a08231 + padded_user_address
   ```
   Returns: number of NFT positions owned.

2. For each position (index 0..n-1), get token ID:
   ```
   tokenOfOwnerByIndex(address, uint256 index)
   data: 0x2f745c59 + padded_user_address + padded_index
   ```

3. Get position details:
   ```
   positions(uint256 tokenId)
   data: 0x99fbab88 + padded_token_id
   ```
   Returns 12 values:
   - [2] `token0` — address
   - [3] `token1` — address
   - [4] `fee` — pool fee tier (500 = 0.05%, 3000 = 0.3%, 10000 = 1%)
   - [5] `tickLower`
   - [6] `tickUpper`
   - [7] `liquidity` — position liquidity

4. Get token0/token1 symbols and decimals
5. Present: pair, fee tier, liquidity, tick range

**Example output:**
```
Uniswap v3 Positions:
  #1: WETH/USDC 0.3% — liquidity: 1,234,567 — ticks: [-887220, 887220] (full range)
  #2: WBTC/WETH 0.3% — liquidity: 456,789 — ticks: [-1200, 1200]
```

## Lido — stETH Staking

Check stETH balance and calculate effective staking rate.

**Contracts (Ethereum only):**
- stETH: `0xae7ab96520DE3A18E5e111B5EaAb095312D7fE84`
- wstETH: `0x7f39C581F595B53c5cb19bD0b3f8dA6c935E2Ca0`

**Steps:**
1. Batch three calls to stETH contract:
   - `balanceOf(address)`: `0x70a08231 + padded_address` → stETH balance (18 decimals)
   - `sharesOf(address)`: `0xf5eb42dc + padded_address` → user's shares
   - `getTotalPooledEther()`: `0x37cfdaca` → total ETH in protocol
   - `getTotalShares()`: `0xd5002f2e` → total shares

2. Calculate exchange rate: `totalPooledEther / totalShares`
3. User's ETH equivalent: `userShares * totalPooledEther / totalShares`
4. stETH/ETH ratio: should be ≈1.0 (rebasing)

**Also check wstETH:**
- `balanceOf(address)` on wstETH contract
- `stEthPerToken()`: `0x035faf82` → wstETH to stETH rate
- User's stETH equivalent: `wstETH_balance * stEthPerToken / 10^18`

**Example output:**
```
Lido Staking (Ethereum):
  stETH balance:  5.234 stETH (≈ 5.234 ETH)
  wstETH balance: 2.100 wstETH (≈ 2.450 stETH ≈ 2.450 ETH)
  Total staked:   7.684 ETH equivalent
  Exchange rate:  1 wstETH = 1.167 stETH
```

## Morpho Blue — Vault Position

Check lending/borrowing position on Morpho Blue.

**Morpho Blue contract:**
- Ethereum: `0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb`
- Base: `0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb`

**Steps:**
1. To query a position, you need the market ID (bytes32). Common markets can be found via `eth_getLogs` for `CreateMarket` events, or use known IDs.

2. Call `position(bytes32 id, address user)`:
   ```
   data: 0x0abe0bfc + market_id (32 bytes) + padded_user_address
   ```
   Returns:
   - [0] `supplyShares` — user's supply shares
   - [1] `borrowShares` — user's borrow shares
   - [2] `collateral` — user's collateral amount

3. To convert shares to assets, call `market(bytes32 id)`:
   ```
   data: 0x44b4bcc2 + market_id
   ```
   Returns market state including `totalSupplyAssets`, `totalSupplyShares`, `totalBorrowAssets`, `totalBorrowShares`.

4. Convert: `userAssets = userShares * totalAssets / totalShares`

**Simpler approach — use MetaMorpho vaults:**
If the user deposited via a MetaMorpho vault (e.g., Gauntlet USDC), check their vault token balance instead:
- `balanceOf(address)` on the vault contract
- `convertToAssets(uint256 shares)`: `0x07a2d13a + padded_shares` → underlying value

## Curve — Pool Info

Get pool composition and virtual price for a Curve pool.

**Common pool addresses (Ethereum):**
- 3pool (DAI/USDC/USDT): `0xbEbc44782C7dB0a1A60Cb6fe97d0b483032FF1C7`
- stETH/ETH: `0xDC24316b9AE028F1497c275EB9192a3Ea0f67022`
- tricrypto2 (USDT/WBTC/WETH): `0xD51a44d3FaE010294C616388b506AcdA1bfAAE46`

**Steps:**
1. Get pool virtual price (exchange rate):
   ```
   get_virtual_price() → 0xbb7b8b80
   ```
   Returns: uint256 (18 decimals). Represents LP token value relative to underlying.

2. Get pool balances per coin (iterate index 0, 1, 2...):
   ```
   balances(uint256 i) → 0x4903b0d1 + padded_index
   ```
   Stop when it reverts (no more coins).

3. Get coin addresses:
   ```
   coins(uint256 i) → 0xc6610657 + padded_index
   ```

4. Get coin symbols/decimals, convert balances
5. Present: pool composition, virtual price, total value

**Example output:**
```
Curve 3pool:
  DAI:  45,234,567.89 (33.2%)
  USDC: 44,891,234.56 (32.9%)
  USDT: 46,123,456.78 (33.9%)
  Virtual price: 1.0312 (LP appreciation: +3.12%)
```

## Tips

- Protocol contracts may differ across networks — always verify addresses before calling
- Use `rpc_batch` to combine multiple calls to the same network
- For multi-network DeFi overview, run each network in parallel
- If a call reverts, the user may not have a position in that protocol
- Aave health factor < 1.0 is critical — highlight this prominently
- Token decimals vary: USDT/USDC = 6, most others = 18, WBTC = 8
