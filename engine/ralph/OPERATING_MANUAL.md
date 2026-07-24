# Ralph Loop — Operating Manual

How to drive the autonomous build loop that constructs `l2arb`. Read this once;
the per-iteration behaviour is governed by `ralph/PROMPT.md` + `CLAUDE.md`.

## What "Ralph" is here

A simple, durable pattern: run the coding agent in a loop, each time with a
**fresh context**, pointed at a **plan on disk**. Because context resets every
iteration, the agent's only memory is the repository — so the plan, the progress
log, and the learnings file must be good enough that a fresh agent can pick up
exactly where the last one left off. One task per iteration keeps context small,
commits atomic, and progress inspectable.

The three moving parts:
- **The plan** (`plan/backlog.md`, `plan/milestones.md`) — what to build, ordered.
- **The constitution** (`CLAUDE.md`) — how to build anything here (rules, TDD,
  data-integrity, scope).
- **The memory** (`ralph/memory/*`) — what's done, what was learned, what's blocked.

## Prerequisites

- `claude` CLI on PATH and authenticated.
- `make setup` has been run once (`uv sync --all-extras`, pre-commit installed).
- For `chain` tests: Foundry/Anvil installed (`curl -L https://foundry.paradigm.xyz | bash`).
- For `db`/`integration` tests: `make services-up` (Postgres/Timescale + Redis).
- Run inside an **isolated environment** (see the safety note in `loop.sh`).

## Starting / stopping

```bash
make loop                 # start the loop (defaults documented in loop.sh)
touch ralph/STOP          # graceful stop after the current iteration
```

Useful overrides (env vars):
```bash
RALPH_MAX_ITERS=50        # cap iterations this run
RALPH_COOLDOWN=5          # seconds between iterations
RALPH_PERMISSION_MODE=acceptEdits   # attended/safer (will prompt on bash)
RALPH_MODEL=<model-id>    # pin a model
RALPH_GREEN_GATE=0        # skip the between-iteration green check
```

Each iteration's full transcript is saved to `ralph/logs/iter-NNNN.log`.

## What one iteration does (summary of PROMPT.md)

1. Orient: read `CLAUDE.md`, memory, backlog, milestones.
2. Ensure `make check` is green (fix first if red).
3. Pick ONE actionable backlog task (or run the scheduled enhancement audit).
4. TDD: write failing tests → minimal elegant code → refactor.
5. Prove: `make check` + the task's tiers (`integration`/`verify`/`bench`) green,
   coverage not dropped.
6. Record: tick backlog, append `progress.md`, add `learnings.md`/ADR, update docs.
7. Commit one atomic change (id in the subject), push, stop.

## Health checks while it runs

- `git log --oneline` — steady stream of small, id-tagged commits.
- `ralph/memory/progress.md` — grows each iteration.
- `plan/backlog.md` — tasks flipping to `[x]` in order.
- `ralph/logs/` — transcripts if you need to see reasoning.
- `make ci` at any time — the tree should be green between iterations.

## Failure modes & what to do

| Symptom | Likely cause | Fix |
|---|---|---|
| Same task attempted repeatedly | acceptance criteria unclear, or a hidden blocker | read the latest `iter-*.log`; sharpen the task in `backlog.md`; check `blocked.md` |
| Tree left red between iterations | a partial change slipped through | next iteration is instructed to fix it first; if stuck, inspect and hand-fix, commit green |
| Loop stops early with `ralph/DONE` | backlog exhausted & milestones met | review; add new backlog items if you want more |
| Agent adds a blocker to `blocked.md` | needs a human decision / credential / out-of-scope ask | resolve the decision, update the task, remove the blocker |
| `chain`/`db` tests skipped | Anvil / services not running | install Anvil, `make services-up`, re-run |
| Thrashing / oscillating design | missing ADR context | ensure `decisions.md` captured the choice; add guidance to `CLAUDE.md` |

## Guardrails already built in

- **Iteration cap** (`RALPH_MAX_ITERS`) — a bug can't loop forever.
- **STOP/DONE sentinels** — clean human stop and clean completion.
- **Green gate** — the tree is checked between iterations; red is surfaced.
- **One task / atomic commit** — easy to inspect and revert.
- **Hard scope rules** in `CLAUDE.md` — no keys, no signing, no synthetic runtime
  data; violations go to `blocked.md` instead of being built.

## Steering the loop

You don't edit the agent — you edit the **plan** and the **constitution**:
- Want a different priority? Reorder `plan/backlog.md`.
- Want a new rule enforced forever? Add it to `CLAUDE.md` (concise) + an ADR.
- Want to pause a risky area? Add a `blocked.md` entry describing why.
- Want more/less thoroughness? Adjust the audit cadence in `CLAUDE.md` §8.
