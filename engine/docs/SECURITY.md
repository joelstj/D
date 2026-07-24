# Security & Safety

> A detection engine has a smaller attack surface than a trading bot, but it is
> not zero. The overriding safety property is **it never holds keys and never
> moves value**; the rest is standard secure-service hygiene plus supply-chain
> care.

## 1. The cardinal safety invariant

**No private keys. No signing. No transaction submission. Ever.**
- `.env.example` contains only read endpoints and tuning knobs.
- `scripts/check_no_secrets.py` (pre-commit + CI) blocks committed private keys,
  mnemonics, and common API-key shapes.
- A static test forbids importing signing primitives (`eth_account.Account.sign*`,
  `send_raw_transaction`) from `src/l2arb`. If a task needs them, it is
  out-of-scope — record in `ralph/memory/blocked.md`.

## 2. Secrets handling

- Secrets come only from environment/`.env` (git-ignored) or a secrets manager in
  prod — never from code, never from logs.
- `structlog` is configured to redact endpoint URLs containing keys and any field
  named like a secret.
- CI runs secret scanning; a hit fails the build.

## 3. Input validation & untrusted data

Everything from outside is untrusted and validated at the boundary:
- **On-chain data** is adversarial: malformed reserves, dust pools, reentrant-
  looking states, tokens with non-standard `decimals`, fee-on-transfer/rebasing
  tokens (which break constant-product assumptions) — detect and quarantine
  these; never let them silently corrupt the graph.
- **RPC responses** are validated (types, ranges, block consistency) via
  `pydantic` before use.
- **API inputs** (the read-only service) are validated and rate-limited; the API
  never accepts anything that changes engine behaviour beyond safe query params.

## 4. Supply chain

- Deps pinned via `uv.lock`; `pip-audit` in CI fails on known CVEs.
- `bandit` static analysis on `src`.
- New dependencies require a one-line justification in the commit and, for
  runtime deps, an ADR. Prefer well-maintained, widely-used libraries.
- Optional heavy/licensed libs (e.g. `arbitragelab`) are isolated to offline
  extras and reviewed for license compatibility before adoption.

## 5. Service hardening (Phase 10/11)

- Read-only API: no mutating endpoints; strict CORS; rate limiting; request size
  limits; no stack traces to clients.
- Run as non-root in the container; minimal base image; drop capabilities.
- Metrics/health endpoints are not exposed publicly without auth.

## 6. Operational safety

- **Fail loud on bad data** (raise, quarantine) but **degrade gracefully on
  infra** (reconnect, failover) — a confused detector must never emit an
  unverifiable opportunity.
- Alerts on data-integrity breaches (two-source disagreement rate), SLO breaches,
  and reorg storms.

## 7. Legal / ethical framing

Arbitrage **detection** from public on-chain data is legitimate market-
microstructure analysis. This project deliberately excludes MEV *extraction*
(sandwiching, front-running) and any execution. It reads only public data and
attacks no one. Keep it that way; scope creep toward extraction/execution is a
`blocked.md` item requiring an explicit human decision, not an autonomous step.

## 8. The security tier in the test suite

- `make audit` = `pip-audit` + `bandit`; part of `make ci`.
- Static tests: no-secrets, no-signing-imports, no-synthetic-data-in-runtime.
- Fuzz/property tests on decoders (malformed log/ABI inputs must not crash the
  event loop or corrupt state).
- `/security-review` is run before each phase-milestone sign-off (see
  `plan/milestones.md`).
