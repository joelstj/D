# Ralph operating rules

These rules govern **every** iteration of the build loop. They exist to keep an autonomous,
fresh-context agent productive and safe over hundreds of iterations. Violating them tends to produce
broken baselines, silent regressions, or unsafe contracts.

## The loop, in one picture

```
        ┌───────────────────────────────────────────────────────────────┐
        │  ralph/loop.sh                                                  │
        │    │                                                           │
        │    ▼                                                           │
        │  feed ralph/PROMPT.md ─► agent (fresh context)                 │
        │                            │                                   │
        │        read RULES/CLAUDE/MEMORY/BACKLOG                        │
        │                            │                                   │
        │        pick ONE task ─► implement ─► scripts/verify.sh (GREEN) │
        │                            │                                   │
        │        check off BACKLOG ─► journal PROGRESS/MEMORY ─► commit  │
        │                            │                                   │
        │    ◄───────────────  restart with fresh context               │
        └───────────────────────────────────────────────────────────────┘
```

The **filesystem is the memory**. Anything not written to `BACKLOG.md`, `PROGRESS.md`, `MEMORY.md`, or
the code/specs themselves is forgotten at the end of the iteration.

## Rules

### R1 — One task per iteration
Pick the single highest-priority unchecked task whose dependencies are all checked. Implement only
that. Batching tasks produces large, unreviewable diffs and hides regressions.

### R2 — Green before done
`bash scripts/verify.sh` (fmt + build + test, plus slither when present) is the definition of done.
A task is not complete and must not be checked off while verify is red. Never `vm.skip`, comment out,
loosen an assertion, or delete a test to force green — fix the code, or fix the test's premise and say
why in `PROGRESS.md`.

### R3 — Spec-driven
Every task cites a spec (`docs/specs/NN-*.md`) and/or a `docs/BUILD_PLAN.md` phase. Read it first. The
spec is the source of truth. If you must deviate, update the spec in the same commit and note it.

### R4 — Tests are not optional
- New external/public function → at least one happy-path and one adversarial test.
- Any arithmetic / pricing / sizing code → a fuzz test (`forge test` with `--fuzz-runs`).
- Any security-critical property (profit invariant, reentrancy, access control, callback origin) →
  an invariant test under `test/invariant/`.
- Integrations with real protocols → a fork test under `test/fork/` (skipped unless `VERIFY_FORK=1`).

### R5 — Safety rails (never cross these)
- No live broadcasts, deploys, or `cast send`. Off-chain, simulate against a fork or `anvil`.
- No secrets in git, logs, or code. Read config from env.
- No fabricated addresses. Placeholders stay flagged until verified against a cited official source.
- No `delegatecall` to untrusted code; no unbounded approvals; validate every external callback's
  `msg.sender` and `initiator`.

### R6 — Keep it lean
Favor small contracts and tight gas on the hot path. Prefer custom errors, packed storage, calldata,
and Yul where it measurably helps — but only with a differential test against a readable reference.
Bulk and cleverness without a test are liabilities.

### R7 — Leave a trail
Update `BACKLOG.md` (check off + add discovered tasks), append to `PROGRESS.md`, and record durable
gotchas in `MEMORY.md`. The next iteration starts blind; your notes are its eyes.

### R8 — Commit atomically
One conventional commit per iteration, referencing the task id (e.g. `[P3-T2]`). The commit should be
green and self-contained.

### R9 — Escalate, don't guess
If a task needs a decision only a human can make (which flash-loan provider to prioritize on a chain
with several, risk tolerances, capital limits, whether to enable a live deploy), do not guess. Write
the question into `PROGRESS.md` under `### NEEDS HUMAN`, add a blocked task to `BACKLOG.md`, and move
to the next available task.

## Modes
Besides the default build prompt (`ralph/PROMPT.md`), focused mode prompts live in `ralph/prompts/`:
`plan.md` (re-plan / groom the backlog), `research.md` (verify external facts & addresses),
`build.md` (implement, the default), `review.md` (adversarial self-review of recent diffs),
`verify.md` (harden tests / gas). Run one with `RALPH_PROMPT=ralph/prompts/review.md bash ralph/run-once.sh`.
