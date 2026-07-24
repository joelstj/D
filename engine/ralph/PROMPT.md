You are one iteration of an autonomous build loop constructing `l2arb`, an
off-chain, near-zero-latency arbitrage **detection** engine for Layer 2
blockchains (detection only — never trading). You have fresh context and no
memory beyond the files in this repository. The filesystem is your memory.

FOLLOW THIS PROCEDURE EXACTLY. Do one task, well, then stop.

## 1. Orient (read these, in order)
- `CLAUDE.md`               ← the operating constitution; it overrides everything
- `ralph/memory/progress.md`  ← what is already done
- `ralph/memory/learnings.md` ← gotchas already discovered — do not relearn them
- `ralph/memory/blocked.md`   ← known blockers — do not bang on them
- `plan/backlog.md`           ← the ordered task list
- `plan/milestones.md`        ← phase acceptance gates

## 2. Make sure the tree is green
Run `make check`. If it is RED, your only job this iteration is to make it green
again: fix it, update progress, commit, stop. A red tree blocks all progress.

## 3. Pick exactly ONE task
From `plan/backlog.md`, take the highest-priority unchecked `[ ]` task whose
`dep:` items are all `[x]`. Respect milestone ordering (don't start Phase N+1
while Phase N's milestone is unmet, unless the task is clearly independent).
If it is an **audit** iteration (see CLAUDE.md §8: every 5th iteration), do the
next rotating enhancement audit instead and file findings as new backlog items.
If the task is too large for one iteration, split it in place into 2–4 finer
`[ ]` sub-tasks and do the first (see `plan/task_template.md`).

## 4. Do the task, TDD-first
- Write the test(s) for the required tiers FIRST; run them; confirm they fail for
  the expected reason.
- Implement the minimum elegant, typed, documented code to pass.
- Refactor. Keep modules small; keep the core pure (no web3 in `amm/`, `graph/`,
  `detect/`).
- Honour the hard rules: only on-chain-verifiable data in runtime paths; no keys;
  no signing; no synthetic data outside tests. If the task would violate these,
  STOP, record it in `ralph/memory/blocked.md`, and pick another task.

## 5. Prove it
- `make check` must pass (lint, types, pairing, tests, coverage floor).
- Also run the tier(s) the task targets: `make integration`, `make verify`,
  and/or `make bench` as applicable. 100% of tests green — no skips-to-pass, no
  xfail hiding a real failure. Coverage must not drop.

## 6. Record what happened (same commit)
- Tick the task `[x]` in `plan/backlog.md` (and any milestone it completes).
- Append a dated entry to `ralph/memory/progress.md` (what/why/evidence).
- Add any durable gotcha to `ralph/memory/learnings.md`.
- Add an ADR to `ralph/memory/decisions.md` if you made an architectural choice.
- Update `docs/` and `CLAUDE.md`/`README.md` if behaviour or setup changed.

## 7. Commit and stop
One atomic commit, imperative subject referencing the task id, e.g.
`feat(amm): out-given-in for constant product [T-0301]`. Push with
`git push -u origin claude/l2-arbitrage-engine-j4olzf`. Then STOP — the
loop restarts you for the next task.

## Completion
If the backlog has no actionable `[ ]` items left and every milestone in
`plan/milestones.md` is `[x]`, write a one-line summary to `ralph/DONE` and stop.

## When blocked
If you cannot proceed safely, append a precise entry to
`ralph/memory/blocked.md` (what, why, what's needed) and pick the next
actionable task. Never fake progress with mocks or stubs that pretend to pass.
