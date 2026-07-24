# Blockers

Things the loop cannot safely resolve on its own. Add precise entries; resolve
and strike through (or remove) when cleared. Never work around a blocker with
fake/mock data or a stub that pretends to pass.

Format:
```
## <ISO date> — <short title>  [status: OPEN|RESOLVED]
What: <the task/attempt>
Why blocked: <the exact obstacle>
Needs: <credential / human decision / tool install / scope ruling>
```

---

## 2026-07-13 — Anvil (Foundry) not installed  [status: OPEN]
What: The `chain` test tier forks a real L2 at a pinned block via Anvil.
Why blocked: Anvil is not in the base image.
Needs: `curl -L https://foundry.paradigm.xyz | bash && foundryup`. Until then,
`chain`-tier tests are skipped (logged as skipped, never counted as passed).
Unit/verify tiers do not require it. This does not block Phase 0–3 unit work.

## 2026-07-13 — Live RPC endpoints not configured  [status: OPEN]
What: Streaming (`integration`/`chain` against live) needs real L2 RPC + WSS URLs.
Why blocked: `.env` is not populated (`.env.example` has placeholders only).
Needs: Human to provide read-only RPC/WSS endpoints for the target chains
(Arbitrum/Base/Optimism). No keys of any other kind are ever needed. Fork-based
`chain` tests can proceed once Anvil is installed even without live endpoints.
