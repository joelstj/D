# Agent operating rules (guardrails)

These are hard constraints for every Ralph iteration. Breaking one is worse than
making no progress.

## Safety

1. **Never enable real on-chain execution implicitly.** `LiveExecutor` stays
   gated. A live send requires BOTH `EXECUTION_MODE=live` (env) AND
   `executionMode: "live"` (settings) AND an explicit backlog task authorizing
   it. Default paths must never move funds.
2. **Never commit secrets.** No private keys, mnemonics, API keys, or `.env`
   files. RPC URLs and keys come from the environment only. If you need a secret
   for a task, read it from `process.env` and document the variable in
   `.env.example`.
3. **No destructive git.** Work on the current branch. Never `push --force`,
   never rebase or amend published commits, never commit directly to `main`.
4. **No mass deletion or rewrites** without an explicit backlog item. Prefer
   additive, reversible changes with clean rollback points.

## Quality

5. **Green before commit.** `bash ralph/verify.sh` must pass. Do not commit with
   failing typecheck, tests, or build.
6. **Tests with behavior.** Any new/changed behavior ships with a test that would
   fail without the change. Bug fixes get a regression test.
7. **No stubs where behavior is expected.** Implement the task fully or reduce
   its scope and note the remainder in the backlog.
8. **Reuse before rebuild.** Search for existing helpers/types/components first.
9. **Small and focused.** One backlog task per iteration; one coherent commit.

## Process

10. **Update the record every iteration**: tick `backlog.md`, append to
    `progress.md`, add discovered follow-ups to `backlog.md`.
11. **When blocked or ambiguous**, write the blocker to `progress.md` and move to
    the next viable task rather than guessing at something irreversible.
12. **Keep the API contract honest.** If you change a REST/WS shape, update
    `backend/openapi.yaml`, the frontend `lib/types.ts`, and the SDKs together.
