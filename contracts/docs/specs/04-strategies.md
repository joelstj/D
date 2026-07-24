# Spec 04 — Arbitrage strategies

A strategy is just a **shape of route**. The executor doesn't hardcode shapes — it executes a route
(ordered hops) and enforces that the loop closes in the borrowed token with profit ≥ `minProfit`. The
off-chain route builders (Phase 5) produce these shapes; the on-chain executor treats them uniformly.

## Invariants common to every same-chain strategy
- **Closes the loop:** hop 1's `tokenIn` == the borrowed/profit token `T`; the final hop's `tokenOut`
  == `T`. The executor verifies this and that ending balance of `T` ≥ starting + loan fee + `minProfit`.
- **Atomic:** one transaction; any shortfall reverts everything (`docs/specs/08`).
- **Sized:** the input notional is set by dynamic sizing (`docs/specs/05`), not chosen arbitrarily.

## 1. Same-chain 2-hop (cross-DEX)
The canonical arb: the same pair is priced differently on two venues.
```
borrow T  →  swap T→U on DEX A  →  swap U→T on DEX B  →  repay T (+fee)  →  keep profit in T
```
Profitable when `outB(outA(x)) > x + fee(x)`. For two constant-product pools the optimal `x` has a
closed form (`docs/specs/05`). "Cross-DEX" just means DEX A ≠ DEX B (e.g. Uniswap V3 vs Aerodrome).

## 2. Same-chain triangular (3-leg cycle)
Exploit a pricing loop across three tokens:
```
borrow T  →  T→U (pool 1)  →  U→V (pool 2)  →  V→T (pool 3)  →  repay T  →  profit
```
Legs may be on the same or different DEXes (triangular ⟂ cross-DEX are orthogonal — a route can be
both). Triangular routes catch cycles that no single pair reveals and are sometimes more capital
efficient. Sizing uses the generic solver (`docs/specs/05`, ternary search) since the composite curve
isn't a simple 2-pool closed form.

## 3. Cross-DEX generalization (n-hop)
The route codec supports up to `MAX_HOPS` (`docs/specs/09`). 2-hop and triangular are the primary,
best-understood shapes; deeper cycles are supported but each extra hop adds gas and slippage, so the
sizer/simulator must show the extra hop pays for itself. Prefer the shortest profitable route.

## 4. Cross-chain 2-hop (non-atomic — Phase 9)
Buy on chain X, sell on chain Y. **Cannot be atomic** (no transaction spans two chains). Handled by a
separate inventory/bridge-intent model with hedging and hard exposure caps — see
`docs/specs/10-cross-chain.md`. Do not shoehorn it into the atomic same-chain executor.

## Orientation & selection
For any cyclic opportunity there are two directions (T→U→T vs the reverse); the builder evaluates both
and submits the profitable one. When multiple venues quote a leg, the builder picks the best-priced
venue *at the sized amount* (best spot price ≠ best at size, because of impact — always evaluate at the
sized notional).

## Route builder responsibilities (off-chain, Phase 5)
1. Enumerate candidate cycles from watched pools (2-hop, triangular, cross-DEX).
2. For each candidate: compute the optimal/size-bounded input (`docs/specs/05`), quote the full path,
   subtract flash fee + gas, and keep only net-profitable candidates.
3. Encode the winning route to bytes (`docs/specs/09`) with per-hop `minOut` and an aggregate
   `minProfit`, then hand to the simulation gate before submission (`docs/specs/11`).

## Negative-path requirements (tests, P5-T4)
- An unprofitable route (spread gone, or gas exceeds gross) **reverts** — never executes at a loss.
- A stale route (price moved between build and inclusion) reverts on `minProfit`/`minOut`/`deadline`.
- A route whose sizing would exceed the price-impact bound is rejected before submission.
These aren't edge cases; they are the normal, expected outcome for most candidates and must be cheap.
