# Arbitrage Theory, Math & Algorithms

> The mathematical spec the code must implement and the tests must pin down.
> Every formula here becomes a unit/property test. Units are stated explicitly
> because unit confusion (wei vs token vs bps) is the dominant on-chain math bug.

## 1. AMM pricing primitives

All amounts are integers in the token's smallest unit (wei-like), converted to
human units only at the reporting edge. `decimals` is read on-chain per token.

### 1.1 Constant-product (Uniswap V2 / Sushi / Aerodrome-vAMM / Camelot-v2)

Reserves `(x, y)` for an ordered pair (in-token X, out-token Y). Pool fee `f`
(e.g. `0.003 = 30 bps`) is taken on input.

- **Marginal (infinitesimal) price** of X in Y: `p = y / x`. The *executable*
  marginal price including fee is `p·(1−f)`.
- **Out-given-in** for input `dx`:
  ```
  dx_eff = dx · (1 − f)
  dy     = (y · dx_eff) / (x + dx_eff)
  ```
- **In-given-out** for desired `dy` (`dy < y`):
  ```
  dx = ceil( x · dy / ((y − dy) · (1 − f)) )
  ```
- **Post-trade reserves**: `x' = x + dx`, `y' = y − dy`. The invariant `x·y`
  weakly increases (fees accrue). A test asserts `x'·y' ≥ x·y`.
- **Fixed-point discipline**: implement fees as integer ratios
  (`feeNum/feeDen`, e.g. `997/1000`) exactly as the contract does, to match
  on-chain results bit-for-bit. Never use float in the executable path.

### 1.2 Concentrated liquidity (Uniswap V3 / PancakeSwap-v3 / Aerodrome-CL)

State: `sqrtPriceX96`, current `tick`, active `liquidity` L, fee tier
(`100/500/3000/10000` = 1/5/30/100 bps), tick spacing.

- **Price from sqrtPriceX96**: `price(token1/token0) = (sqrtPriceX96 / 2^96)^2`,
  then scale by `10^(dec0 − dec1)` for human units.
- **Within a single tick** (liquidity constant), swap math (from the V3
  whitepaper §6.2–6.3):
  - token0 → token1: `Δ(1/√P) = Δx / L` ⇒ new `√P`; `Δy = L·Δ(√P)`.
  - token1 → token0: `Δ(√P) = Δy / L`; `Δx = L·Δ(1/√P)`.
- **Crossing ticks**: when a swap exhausts the range to the next initialized
  tick, apply net liquidity change `liquidityNet` and continue. Requires the
  **tick bitmap** and per-tick `liquidityNet`, read on-chain.
- **Validation strategy**: implementing full tick-crossing is error-prone, so the
  spec is: implement local math **and** call the on-chain **QuoterV2**
  (`quoteExactInputSingle`) via `eth_call`; a `verify` test asserts
  `local == quoter` for a battery of sizes and pools. Local math is used at
  runtime for latency; the quoter is the oracle of record. **Realized:** the
  engine reproduces a live Base Uniswap V3 WETH/USDC pool's QuoterV2 output
  **bit-for-bit (0 wei)** in both directions, including the post-swap
  `sqrtPriceX96` (`tests/verify/test_onchain_amm.py`; captured via raw `eth_call`).
- **Crossing safely**: rather than guess beyond the active tick, each swap accepts
  an optional `sqrtPriceLimitX96` and is **capped at the boundary** if it would
  cross — the returned output is then a *lower bound*. Understating output can only
  suppress an opportunity, never fabricate one (the safe direction for a detector).

### 1.3 StableSwap (Curve) — **delivered** (`amm/stableswap.py`)

Invariant for `n` coins with amplification `A`:
```
A·n^n·Σx_i + D = A·D·n^n + D^(n+1) / (n^n · Π x_i)
```
Solve `D` by Newton iteration, then solve the output coin balance for a given
input by Newton iteration (2-coin specialization, `Ann = A·N_COINS`). Matches
Curve's integer rounding including the `dy = balanceOut − y − 1` output
convention. Property-tested: swapping ε in and back out loses only the fee; both
Newton loops converge on pathological amp/imbalance (adversarial stress tier).

### 1.4 Weighted pools (Balancer) — **delivered** (`amm/weighted.py`)

Spot price `= (B_i / W_i) / (B_o / W_o)`. Out-given-in:
```
A_o = B_o · ( 1 − ( B_i / (B_i + A_i·(1−f)) )^(W_i / W_o) )
```
The fractional power `(W_i/W_o)` is computed with `Decimal` at 60-digit precision
via `(exp·ln)` and **floored** — output is never overstated. A swap can never
drain the pool, so the result is clamped to `B_o − 1`: at extreme weight ratios a
huge input makes the power underflow to 0, which would otherwise round output up
to the entire out-balance (caught by the adversarial stress tier).

## 2. The exchange-rate graph

Model each chain as a directed multigraph `G = (V, E)`:
- `V` = tokens on that chain (canonicalized addresses).
- For every pool and every ordered direction, an edge `X → Y` carrying the
  **executable marginal rate** `r = (∂Y/∂X)|_{dx→0} = (y/x)(1−f)` for V2, or the
  V3 marginal, plus a reference to the pool for exact sizing later.

**Key transform:** set edge weight `w(X→Y) = −ln(r)`. Then for any cycle
`C = v0 → v1 → … → v0`:
```
Σ_{e∈C} w(e) = −ln( Π r_e )
```
So **a profitable cycle (product of rates > 1) ⇔ a negative-weight cycle**
(`Σ w < 0`). This single identity underlies all three single-chain detectors.

> Caveat the code must respect: marginal rates ignore size/price-impact. The
> graph search is a **candidate generator**; every candidate cycle is then
> **re-priced with full AMM math at an optimized trade size** (§4) before it is
> reported. A cycle that looks profitable at the margin can be unprofitable once
> slippage and gas are included.

## 3. Detection algorithms

### 3.1 2-hop / spatial (a 2-cycle)
Same pair `(A,B)` on two pools P1, P2. Opportunity if
`rate_{A→B}(P1) · rate_{B→A}(P2) > 1` (buy B where cheap, sell where dear).
Directly a length-2 negative cycle. O(pairs) scan; trivial and fast — always on.

### 3.2 Triangular (a 3-cycle)
`A → B → C → A` with product of rates > 1. Enumerate over the pool set with a
bounded search rooted at "hub" tokens (WETH, USDC, USDT, DAI) where most
liquidity concentrates, to keep the branching factor small.

### 3.3 Multi-hop / bounded negative cycle
General cycles up to `MAX_HOPS` (config, default 4). Two complementary methods:

- **Bellman–Ford / SPFA** on `−ln` weights. Relax `|V|−1` rounds; a further
  successful relaxation exposes a negative cycle; recover it via the predecessor
  chain. SPFA (queue-based) with parent-tracking gives early exit. Complexity
  `O(V·E)` worst case; fine for incremental/localized runs.
- **Tropical (min-plus) matrix K-hop.** Let `Wᵏ` be the min-plus matrix power of
  the weight matrix. `Wᵏ[i][i] < 0` ⇒ a negative cycle of length ≤ k through `i`.
  `k` products cover all cycles up to length `k`. This is dense, vectorizable
  with `numpy`/`numba`, and naturally **bounds hop count** — which we want for
  latency and for realistic gas. Used for the periodic full sweep; SPFA for the
  incremental per-block updates.

### 3.4 Incremental re-search (the latency trick)
On each block/log batch, only pools whose reserves changed produce **dirty**
token nodes. Re-run bounded search seeded from dirty nodes over their
neighborhood instead of the whole graph. Maintain the graph in place (O(1) edge
update per `Sync`/`Swap`). This turns per-block work from `O(V·E)` into roughly
`O(dirty · degree^hops)`.

## 4. From candidate cycle to reported opportunity

A candidate cycle is only an opportunity after **exact, size-aware** evaluation:

### 4.1 Profit as a function of size
For input size `s` routed through the cycle with full AMM math, define
`profit(s) = out(s) − s − cost(s)` in the numeraire token. Ignoring gas,
`profit` is **concave** (rises, peaks, then falls as price impact grows).

### 4.2 Optimal size
- **Two constant-product pools** (classic 2-hop): closed-form optimum exists —
  implement and unit-test it against the general solver.
- **General cycle**: **memoized golden-section search** on `s` over `[0, s_max]`
  where `s_max` is bounded by pool liquidity (auto-bracketed by doubling). Each
  step narrows the bracket by the golden ratio with the carried probe served from
  a memo (~1 new route eval/step); probes are recomputed fresh from the live
  bracket so integer rounding cannot drift them into crossing. A final exact
  integer brute-force over the small residual window pins the true argmax, so
  correctness never rides on the float ratio. Concavity guarantees convergence.

### 4.3 Net profitability (the report gate)
```
net = out(s*) − s* − gas_cost_numeraire − bridge_cost(if x-chain)
```
- **Gas**: model L2 gas = execution gas + L1 data-availability component; convert
  native-token gas to the numeraire using an on-chain price (never a guess).
  Apply `GAS_SAFETY_MULTIPLIER`.
- Report iff `net ≥ MIN_PROFIT_BPS · s*` **and** `net > 0` after the safety
  haircut. Attach `{chain, block_number, block_ts, pools[], size, gross, gas,
  net, hops}` provenance.

## 5. Cross-chain 2-hop (simple only)

- **Asset identity** is the hard part: the "same" asset has different addresses
  per chain and native-vs-bridged variants (USDC vs USDC.e, WETH per chain). A
  curated, on-chain-verified `CanonicalAsset` map defines which (chain, address)
  pairs are fungible for arbitrage purposes. Bridged variants are treated as
  distinct unless a bridge makes them 1:1 with negligible risk — recorded
  explicitly, never assumed.
- **Spread**: price the asset in a common numeraire (a stable) on chain A and
  chain B from live pools. Candidate if
  `price_B − price_A > bridge_cost + fees + slippage(both legs)`.
- **Bridge model**: cost (fee + gas both sides) and **settlement time**. Because
  settlement is not atomic, the report includes `time_to_settle` and a
  price-drift risk note — this is a *detected spread*, not a guaranteed capture.
- No cross-chain triangular/multi-hop (scope).

## 6. What the tests must pin (see docs/TESTING_STRATEGY.md)

- AMM formulas vs hand-computed values and vs on-chain quoter (`verify`/`chain`).
- Graph identity: `Σ −ln r < 0 ⇔ Π r > 1` (property test).
- Detectors find known-planted cycles in synthetic graphs; find none in
  arbitrage-free graphs (no false positives).
- `optimal_size` maximizes `profit` (compare vs brute-force grid; concavity).
- Net-profit gate never reports a cycle that loses money once gas+slippage
  applied (property test over random-but-valid pool states).
- Incremental search result ≡ full search result (equivalence test).
