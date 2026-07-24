# Spec 05 — Dynamic loan sizing

**The headline feature.** Borrow *exactly* as much as the pools can absorb profitably: scale **up** in
deep liquidity, **down** in thin liquidity, so the trade maximizes net profit without moving the price
enough to fail on slippage/`minOut`. Under-borrowing leaves profit on the table; over-borrowing eats
its own edge through price impact and can revert the whole trade.

## The size is a minimum of four caps

```
size = min(
    x_optimal,        # profit-maximizing input (closed form or search)
    x_impact,         # largest input keeping price impact <= bound p
    x_flashLiquidity, # borrowable amount available from the chosen provider
    x_riskCap         # human-set max notional / capital limit  (NEEDS HUMAN)
)
and require:  grossProfit(size) - flashFee(size) - gasCost >= minProfit,  else skip.
```

`x_optimal` and `x_impact` **both scale with reserves**, which is exactly the "bigger loan when liquidity
is high, smaller when low" behavior — it falls out of the math, it isn't a heuristic knob.

## Cap 1 — profit-optimal input

### Two constant-product pools (closed form)
Route: `token0 → (pool A) → token1 → (pool B) → token0`. Reserves: pool A `(a0, a1)`, pool B `(b1, b0)`
(input-side, output-side). Fee factor `γ = 1 − fee` per pool (`γ = 0.997` for 0.3%). Composing the two
CPMM swaps gives a fractional-linear map `z(x) = M·x / (N + K·x)` with

```
M = γ_A · γ_B · a1 · b0
N = a0 · b1
K = γ_A · (b1 + γ_B · a1)
```

Profit `π(x) = z(x) − x` is maximized where `π'(x) = M·N/(N+Kx)² − 1 = 0`, giving the **closed form**

```
x_optimal = ( sqrt(M · N) − N ) / K
```

An opportunity exists (and `x_optimal > 0`) **iff** `M > N`, i.e. `γ_A·γ_B·a1·b0 > a0·b1`. This is the
cheap pre-filter the scanner runs before any heavier work. (Implement in integer math with a `sqrt`
that rounds down; unit-test against a high-precision reference — task **P6-T2**.)

### Concentrated liquidity (Uniswap V3 / Slipstream / Algebra)
No single closed form across ticks. Within the **active tick** (constant `L`), moving the price between
`√P` values moves token amounts by
```
Δx (token0) = L · (1/√P_target − 1/√P_current)
Δy (token1) = L · (√P_target − √P_current)
```
For larger inputs the swap crosses ticks and `L` changes; the sizer walks ticks (reusing the quoter)
accumulating input until the marginal price equals the other venue's marginal price (the arb
first-order condition: equalize marginal prices across the two venues net of fees). Task **P6-T3**.

### Mixed / triangular / arbitrary curves — generic solver
When the path mixes families or has ≥3 legs, `π(size)` has no tidy closed form but is **unimodal** in
`size` for realistic arb (rises then falls). Use **ternary search** over `[0, sizeCapUpper]` calling the
composed `quote()` chain, converging to `x_optimal` in ~40–60 iterations to 1e-9 relative. Task **P6-T4**.
This is the universal fallback and also a cross-check on the closed form.

## Cap 2 — price-impact bound (the anti-deviation guard)

Bound the price deviation each hop causes so the trade doesn't "alter the pricing so much as to fail."
For a constant-product pool, input `dx` into input reserve `x` moves the marginal price by roughly
`dx/(x+dx)`. To keep impact ≤ `p` (e.g. `p = 0.003` = 30 bps):
```
x_impact = x · p / (1 − p)        # directly proportional to reserve x → deeper pool, bigger allowed loan
```
For concentrated liquidity, translate `p` into a `√P` bound and cap the input to what keeps `√P` within
`√P_current · sqrt(1 ± p)` given active-tick `L`. The bound is enforced **twice**: off-chain when sizing,
and **on-chain** by `SizingGuard` (`minOut` per hop + max-impact) so a stale or manipulated route
reverts rather than executes into bad pricing. Task **P6-T1**.

`p` is a per-strategy config (`config/strategies.example.json`), tighter for volatile/thin pairs.

## Cap 3 — flash liquidity available
Clamp to what the selected provider can actually lend for that token right now (`docs/specs/02`). If the
sized amount exceeds every provider's available liquidity, either split across providers (advanced) or
reduce to the max borrowable and re-check profitability.

## Cap 4 — risk cap (human-set)
A hard max notional per trade and per chain, plus a max fraction of pool reserves regardless of the
impact math. **NEEDS HUMAN**: these limits are a risk decision, not a computed value. Defaults are
proposed in `config/strategies.example.json` and must be confirmed before live use.

## Gas-adjusted breakeven (the go/no-go)
Sizing that maximizes gross profit is pointless if gas eats it. Compute `gasCost` in profit-token units
using the **per-stack** fee model (OP-Stack L1 blob data fee vs Arbitrum poster fee — `docs/specs/01`,
`07`) at the *actual* route calldata size, then require
```
netProfit(size) = grossProfit(size) − flashFee(size) − gasCost  ≥  minProfit
```
There is also a **minimum** profitable size (below which gross < gas). The feasible region is
`[x_breakeven_low, min(caps)]`; pick `x_optimal` clamped into it, or skip if empty.

## Properties to prove (fuzz/invariant, P6-T6)
- **Monotone in depth:** scaling all reserves by `k>1` scales the sized amount up (never down).
- **Impact bound holds:** the realized post-trade price deviation ≤ `p` for the chosen size, on every
  hop, across families.
- **Never lossy:** for any inputs, either `netProfit ≥ minProfit` or the route is skipped/reverts.
- **Closed form ≈ search:** `x_optimal` (CPMM formula) matches the ternary-search result within
  tolerance on random reserves/fees.

## Worked intuition
Deep pool (reserves 10,000 WETH): 30-bps impact bound allows ~30 WETH input. Thin pool (reserves 100
WETH): the same bound allows ~0.3 WETH. Same code, same `p` — the loan auto-scales two orders of
magnitude with liquidity. That is the requirement, satisfied by `x_impact ∝ reserve` and
`x_optimal ∝ reserves`.
