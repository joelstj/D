# Spec 06 — MEV, ordering, frontrun/backrun protection & tipping

The strongest MEV defense is structural: **the transaction reverts unless it nets a profit**, so an
adversary can never make us execute a losing trade, and a stolen/replayed route only wins if it is still
profitable at inclusion. Everything else here reduces the *chance of being beaten to it* and the *cost
of winning*.

## L2 ordering reality (per `docs/specs/01`)
- **OP Stack (Optimism, Base, Ink, Unichain):** today a single sequencer with a **private mempool** and
  first-come ordering. There is no public pending-tx mempool to snipe from, so classic public-mempool
  frontrunning/sandwiching is largely absent. Residual risks: the sequencer's own position, latency
  races among searchers hitting the same endpoint, and future decentralized-sequencer designs.
- **Arbitrum One:** fast sequencer **plus Timeboost**, an express-lane auction granting the winning
  bidder a small latency edge. It is both a way for us to win inclusion and an MEV surface to model.

Because ordering differs, the **submission adapter is pluggable** (`SubmitMode`): `private` /
`sequencer-direct`, `backrun`, `timeboost`, `public` (fallback). Keep alpha off the public mempool.

## Defenses

### 1. Atomic revert (primary)
Aggregate `minProfit` + per-hop `minOut` + `deadline`. If the realized edge is gone at inclusion, the
tx reverts and we pay only gas. This neutralizes: being frontrun (spread taken → we revert), stale
routes, and most manipulation. It is enforced on-chain and cannot be skipped.

### 2. Private / sequencer-direct submission
Submit through a private endpoint or directly to the sequencer rather than a public RPC that could leak
the route. Configure per chain in `.env` (`*_PRIVATE_RPC_URL`). This protects the *strategy* (which
pools/route) as much as the *trade*.

### 3. Backrunning support (we are usually the backrunner)
Most arb is a **backrun**: a large swap moves a pool, and we immediately trade behind it to restore
parity and capture the spread. The submitter supports **backrun bundles** — "include my tx immediately
after tx X" — where the chain/relay offers it. First-class, not an afterthought (**P7-T2**).

### 4. Not being sandwiched ourselves
Our own trade is uneconomical to sandwich because it reverts if pushed below `minProfit`, and private
submission keeps it out of a public mempool. We never rely on a manipulable spot price for settlement —
settlement is the realized on-chain output, checked against `minOut`.

### 5. Freshness / anti-replay
`deadline` (unix seconds) and optional block-tag bounds reject stale inclusion. Routes carry a hash;
the executor emits it (`ArbExecuted`) for monitoring and replay detection (**P7-T5**).

## Priority fee & tip strategy (per stack)
Winning ordering costs money; overpaying burns the edge. The tip module caps the bid to a **fraction of
expected profit** and models each stack:
- **OP Stack:** EIP-1559 style — `maxPriorityFeePerGas` influences ordering and is paid to the
  sequencer; the L1 data fee dominates total cost. Tune priority within `tip ≤ α · expectedProfit`
  (α configurable, default conservative). Compact calldata keeps the dominant L1 term down (`docs/specs/07`).
- **Arbitrum:** priority-fee semantics differ; when latency is the binding constraint, **Timeboost**
  bidding (P7-T3) can beat raw fee bumping. Bid only up to the profit-fraction cap.
The cap is derived from the sized route's simulated net profit (`docs/specs/05`), so we never tip more
than the trade is worth. **NEEDS HUMAN:** the profit-fraction `α` and any absolute tip ceiling.

## Explicitly out of scope (for the executor)
Generalized frontrunning of *others*, sandwich *attacks*, and liquidation MEV are not this component's
job — it is a defensive, profit-or-revert arb engine. Backrunning our own opportunity is in scope;
predatory strategies are not.

## Deliverables (Phase 7)
Tip module (per-stack, profit-capped) · private/sequencer-direct + backrun submission adapter ·
Arbitrum Timeboost path · freshness/deadline guards · documented per-chain submission strategy.
