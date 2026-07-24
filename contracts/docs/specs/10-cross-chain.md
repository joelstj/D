# Spec 10 — Cross-chain 2-hop arbitrage

**Read this first: cross-chain arbitrage is not atomic.** No single transaction spans two chains, so the
"borrow → trade → repay, or revert" guarantee that protects same-chain strategies **does not exist**
here. Between buying on chain X and selling on chain Y, price can move, a bridge can stall, and capital
is exposed. This is the highest-risk component and is deliberately **last** (Phase 9), conservative, and
gated by human-set caps.

## Two models

### A. Inventory (pre-positioned) model — preferred
Hold working inventory of the traded assets on **both** chains up front. When a cross-chain spread
appears: sell on the expensive chain and buy on the cheap chain **using local inventory on each**,
roughly simultaneously. No user waits on a bridge in the critical path; bridging is used only to
**rebalance** inventory afterward, off the hot path.
- **Pro:** near-instant capture, no in-flight bridge risk per trade.
- **Con:** capital is tied up on multiple chains; inventory drifts and must be rebalanced and hedged;
  requires a funded, risk-capped treasury.

### B. Bridge/intent (just-in-time) model
Execute the first leg, then move value via a **fast bridge or intent/solver network** (a solver fronts
liquidity on the destination against a source-chain guarantee), then execute the second leg. The native
canonical bridge is the slow, trust-minimized fallback; fast bridges/intents trade some trust for speed.
- **Pro:** less standing capital.
- **Con:** per-trade bridge latency + failure/refund risk; solver/bridge counterparty risk; the spread
  can vanish before settlement.

The system implements **A first** (lower per-trade risk) with **B** behind a common adapter for
opportunistic use.

## Components (Phase 9)
- **Settlement contracts per leg** (`deposit`/`claim`/`refund`) with clear ownership and timeouts, so a
  failed cross-chain attempt **refunds** rather than strands funds (**P9-T2**).
- **Bridge/intent adapter interface** — one API over canonical bridges, fast bridges, and intent/solver
  networks; each concrete adapter behind it, mock + one real on a fork (**P9-T3**).
- **Off-chain orchestrator** — watches spreads across chains, checks inventory, sizes within exposure
  caps, executes both legs, initiates rebalancing, and **hedges** net directional exposure created in
  the gap (**P9-T4**).

## Risk controls (mandatory, not optional)
- **Hard exposure cap** per route and per chain — max value in-flight/unsettled at any moment. **NEEDS
  HUMAN.** The orchestrator refuses opportunities that would exceed it.
- **Settlement policy / finality:** define when a source-leg fill is "safe enough" to act on the
  destination (soft-confirm vs finalized), per `docs/specs/01`. Never treat an unfinalized fill as
  settled beyond the cap.
- **Hedging:** any net directional position opened while legs/bridge are outstanding is hedged (e.g. on
  a perp/spot venue) so a price move during the gap doesn't turn a spread capture into a loss.
- **Timeouts + refunds:** every cross-chain step has a deadline; on breach, unwind/refund deterministically.
- **Inventory accounting & alerts:** track balances per chain, drift, and rebalance cost; alert on drift
  beyond a threshold (`docs/specs/11`).
- **Kill switch:** the orchestrator honors the same pausable/circuit-breaker posture as the on-chain
  executor.

## What we explicitly do NOT do
- No pretending cross-chain is atomic. No "fire and hope" without hedging and caps.
- No unbounded reliance on a single bridge — the adapter abstracts multiple, and canonical is the
  trust-minimized backstop.
- No live cross-chain capital before the same-chain engine is audited and the caps/hedging are
  human-approved.

## Testing
Simulate both legs on forks of both chains; simulate bridge success, delay, and failure/refund paths;
assert inventory accounting is exact and exposure never exceeds the cap in a stateful invariant test.
Cross-chain messaging is mocked deterministically in tests (no live bridge calls in the loop).
