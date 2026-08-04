# Spec 08 — Security & threat model

An arbitrage executor holds approvals, receives untrusted callbacks, and touches adversarial contracts
(pools, tokens, hooks). It must be **secure by construction**: a small, hardened core with proven
invariants, not a large surface patched after the fact.

## Assets at risk
- Flash-loan principal in-flight, and any token balances/approvals the executor holds mid-trade.
- The operator's gas.
- (Cross-chain) inventory held on each leg (`docs/specs/10`).

## Adversaries & attack surface
| Adversary | Vector | Mitigation |
|-----------|--------|------------|
| Arbitrary caller | Invoke `execute` or a callback directly | Operator allowlist on `execute`; callback validates caller+initiator |
| Malicious/forged pool | Impersonate a flash/swap callback to drain | Assert `msg.sender ==` canonical pool (derived from factory/registry) **and** `initiator == address(this)` |
| Reentrancy | Re-enter via a callback or token hook | Transient (or storage) reentrancy guard around the whole `execute` and callback |
| Malicious token | Fee-on-transfer, rebasing, missing return, reentrant `transfer` | Measure balance deltas; `SafeTransfer`; guard; per-hop `minOut` |
| Malicious V4 hook | Grief/steal inside a hooked pool swap | Prefer allowlisted/known hooks; treat unknown hooks as hostile |
| Sequencer / ordering | Reorder or exclude | Atomic revert makes reordering non-lossy; private submission (`docs/specs/06`) |
| Price manipulation | Move a pool to fake an opportunity | We never settle on a manipulable spot; realized output is checked vs `minOut`; sizing bounds impact |
| Operator key theft | Submit trades / drain rescue | KMS/hardware signer; least-privilege operator; owner-only rescue; pausable |
| Malicious/unauthorized bridge adapter | `CrossChainArbitrageExecutor.executeSourceLeg`'s caller-supplied `bridgeAdapter` is `forceApprove`d for the entire held balance of `bridgeToken` and called with `msg.value` — an attacker-controlled adapter could drain the full inventory | `allowedBridgeAdapters` guardian-gated allowlist, deny-all by default (mirrors `FlashLoanArbitrage.allowedGenericRouters`); see `docs/specs/10-cross-chain.md` |

## Core invariants (must be proven, not assumed)
1. **Profit invariant.** For every successful `execute`, the executor/recipient ends with ≥ its
   starting balance of the profit token + loan fee + `minProfit`. No path leaves it worse off.
2. **No token loss.** No `execute` (success *or* revert) can leave a non-profit token stranded or a
   standing approval that lets a third party pull funds afterward.
3. **Callback authenticity.** A flash/swap callback executes its body **only** if caller+initiator
   checks pass and an in-flight guard set by *this* transaction is present.
4. **Single entry / no reentry.** The guard prevents nested `execute`/callback execution.
5. **Access control.** Only allowlisted operators call `execute`; only owner pauses/rescues/manages the
   allowlist; owner transfer is 2-step.
6. **Route-codec round-trip.** decode(encode(r)) == r for all valid routes; malformed routes revert
   cleanly (`docs/specs/09`).

## Structural defenses
- **Thin core, pure adapters** — the audited surface is one small contract (`docs/specs/03`, `ARCHITECTURE.md`).
- **No `delegatecall` to untrusted code.** Adapters are trusted, deployed code called via interface.
- **Approval hygiene** — exact/transient approvals scoped to a call; never unbounded; reset where a
  token requires it.
- **Pausable circuit breaker** + **owner-only rescue** for tokens accidentally sent to the contract.
- **Immutability preferred.** If upgradeability is ever added, it is behind a timelock + multisig, and
  that decision is **NEEDS HUMAN**. Default: non-upgradeable.
- **Deadlines & freshness** on every route (`docs/specs/06`).

## Testing pyramid (the definition of "secure enough to consider")
1. **Unit** — happy + adversarial per function (missing-return token, zero liquidity, tick boundary,
   max-uint, direct-callback attempt).
2. **Fuzz** — all pricing/sizing/codec math (`forge test`, hundreds+ runs).
3. **Invariant** (`test/invariant/`) — the six invariants above via Foundry invariant testing; a
   stateful actor model that tries to break them (**P2-T7, P6-T6, P10-T1**).
4. **Property/Echidna** — long campaigns on the executor + sizer (**P10-T1**).
5. **Symbolic** — Halmos/Certora proof of the profit invariant and codec round-trip (**P10-T3**).
6. **Static** — Slither + Semgrep clean; every remaining finding triaged and justified (**P10-T2**).
7. **Fork** (`test/fork/`, `VERIFY_FORK=1`) — real pools/providers on each chain.
8. **Differential** — every assembly block vs a Solidity reference (`docs/specs/07`).

## Operational security
- No secrets in git/logs/code; keys in a KMS/hardware signer (`.env` is local-only, git-ignored).
- The Ralph loop **never broadcasts**; deploys are human-gated (`ralph/OPERATING_RULES.md` R5).
- Monitoring: revert-rate (high is normal/healthy), realized profit, unexpected balance changes,
  inventory drift (`docs/specs/11`).

## The hard gate before mainnet capital
**An external audit is required before any non-trivial mainnet deployment.** Phase 10 produces the
audit-prep packet (scope, invariants, threat model, known limitations). Commissioning the audit and
authorizing live capital are **NEEDS HUMAN** decisions. Until then: testnet, forks, and dust only.

## Known limitations (keep current)
- Cross-chain settlement is non-atomic and carries inventory + bridge risk (`docs/specs/10`).
- V4 hooked pools can embed arbitrary logic; only allowlisted hooks should be routed through.
- New chains (Ink, and any others) may lack mature providers/venues and battle-tested addresses —
  Phase 1 verification gates their inclusion.
