# Spec 03 — DEX adapters

Every venue sits behind one interface. The executor dispatches a hop to an adapter; the adapter knows
the venue's swap call and quote math. Adding a DEX = adding an adapter + a config entry. Addresses live
in `config/chains/*.json` (verify in **P1-T5**).

## The uniform interface

```solidity
interface IDexAdapter {
    /// @notice Swap exactly `amountIn` of `tokenIn` for `tokenOut` on `pool`, requiring >= `minOut`.
    /// @param  data  venue-specific params (fee tier, poolKey, bin ids, hooks) from the route bytes.
    /// @return amountOut actual output, sent to `to`.
    function swap(address pool, address tokenIn, address tokenOut, uint256 amountIn, uint256 minOut, address to, bytes calldata data)
        external returns (uint256 amountOut);

    /// @notice Pure/view quote used off-chain and by on-chain sizing guards.
    function quote(address pool, address tokenIn, address tokenOut, uint256 amountIn, bytes calldata data)
        external view returns (uint256 amountOut);
}
```

**Parity rule:** `quote()` must match the realized `swap()` output on a fork within a tight tolerance
(rounding only). Each adapter ships a fork test asserting parity (**P4-\***). A drifting quote breaks
sizing and simulation.

## Families & math

### 1. Uniswap V2 / Solidly constant-product (vAMM)
`x·y = k`. Output for input `dx` with fee `f` (e.g. 0.3% → `f = 997/1000`):
```
amountOut = (reserveOut · dx · f) / (reserveIn·1 + dx · f)          // integer form: numerator/denominator
```
Solidly forks (Velodrome/Aerodrome **volatile** pools, Camelot V2) use the same curve; **stable** pools
use the Solidly `x³y + y³x = k` curve — the adapter must branch on the pool's `stable` flag and use the
correct `getAmountOut`. Read reserves via `getReserves()`; respect token ordering.

### 2. Uniswap V3 (concentrated liquidity)
Swaps cross ticks; exact output is not a simple closed form across a range. Adapter calls the
SwapRouter/pool `exactInputSingle`-style path with `sqrtPriceLimitX96` as a guard. For quotes use the
Quoter (or an in-house tick-walking quoter for gas-free `view` sizing). Fee tiers (100/500/3000/10000)
come from the route bytes. Concentrated-liquidity **depth** feeds dynamic sizing (`docs/specs/05`).

### 3. Uniswap V4 (singleton PoolManager)
No per-pair pool contract: a single `PoolManager` holds all pools, swaps happen inside an `unlock`
callback using **transient** flash accounting (settle deltas, don't transfer per hop). Pools are keyed
by `PoolKey{currency0, currency1, fee, tickSpacing, hooks}`. Hooks can alter fees/behavior — the adapter
must read the pool's hook and treat hooked pools cautiously (a malicious hook can grief a swap; prefer
known/allowlisted hooks). This adapter composes with the V4 flash-accounting borrow (`docs/specs/02`).

### 4. Solidly-CL / Slipstream (Velodrome, Aerodrome)
Concentrated-liquidity forks of the Uniswap-V3 model with tickSpacing-based pools and their own
factory/gauge system. Math mirrors V3; addresses and the quoter differ. Adapter reuses the V3 tick
quoter with the fork's constants.

### 5. Algebra (Camelot V3, Arbitrum)
Concentrated liquidity with a **dynamic fee** set by the pool's Algebra plugin rather than a fixed tier.
The adapter must read the current fee from the pool/plugin at quote time; do not assume a static tier.

### 6. Curve StableSwap
Stable/mixed pools using the StableSwap invariant. Use `get_dy(i, j, dx)` for quotes and `exchange(i, j,
dx, minOut)` for swaps, indexing coins by their pool index (not address). Handle pools with different
`A` and fee params; some are `int128` indexed, some `uint256` — branch per pool type.

### 7. Balancer V2 (Vault + pools)
All pools share the `Vault`; swap via `Vault.swap` / `batchSwap` with `SingleSwap`/`BatchSwapStep`
structs and a `FundManagement`. Weighted, Stable, and Composable-Stable pools have different math;
Balancer's own SDK/`queryBatchSwap` gives quotes. The adapter targets the Vault, not individual pools.

### 8. TraderJoe Liquidity Book (Arbitrum)
Discretized "bins" of liquidity at fixed prices; swaps consume bins. Quote via the LB quoter/router;
the adapter accounts for bin steps and active-bin liquidity. Depth for sizing = liquidity in the bins a
trade would traverse.

## Per-chain venue presence (verify in P1-T5)

| Chain | Likely families present (confirm addresses) |
|-------|---------------------------------------------|
| Optimism | Uniswap V3/V4, Velodrome (v2 + Slipstream CL), Curve, Balancer |
| Base | Uniswap V3/V4, Aerodrome (v2 + Slipstream CL), SushiSwap, PancakeSwap, Balancer |
| Ink | Newer chain — enumerate live DEXes and verify each address before adding an adapter |
| Unichain | Uniswap V4 (primary), Uniswap V3 |
| Arbitrum One | Uniswap V3/V4, Camelot (v2 + Algebra v3), Balancer, Curve, TraderJoe LB, SushiSwap |

Do not add an adapter to the shipped set for a (chain, DEX) pair whose addresses are still
`"_verify": true`. Verifying them is a Phase-1 research task with cited sources.

## Robustness requirements (every adapter)
- Support **fee-on-transfer / rebasing** tokens by measuring balance deltas (`balanceAfter -
  balanceBefore`), never trusting a return value or the nominal `amountIn`.
- Enforce `minOut` at the adapter boundary as defense-in-depth (the executor also enforces aggregate
  `minProfit`).
- Never grant unbounded approvals; scope to the swap and reset per venue requirements.
- Treat token ordering, decimals, and index-vs-address addressing explicitly — a silent mismatch is a
  correctness bug, not a revert.
