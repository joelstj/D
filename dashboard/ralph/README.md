# Ralph — autonomous build harness

This directory is a [Ralph loop](https://ghuntley.com/ralph/): you point a coding
agent at one prompt and run it in a loop. Each iteration starts with a **fresh
context**, reads its memory from disk (backlog, progress log, git history), does
**one** verified unit of work, records it, and commits. Over many iterations the
loop builds the product toward `SPEC.md`.

## Files

| File | Role |
|------|------|
| `PROMPT.md` | The prompt fed to the agent every iteration. The core instructions. |
| `SPEC.md` | What we are building and the acceptance bar. The north star. |
| `backlog.md` | Prioritized, checkbox task list. The loop takes the top open item. |
| `progress.md` | Append-only log — the loop's long-term memory across iterations. |
| `AGENT.md` | Hard guardrails (safety, quality, process) the agent must not break. |
| `verify.sh` | Typecheck + tests + build gate. Must pass before a commit counts. |
| `ralph.sh` | The loop runner. |

## Run it

```bash
# From the repo root. Requires a coding-agent CLI on PATH (default: claude).
ralph/ralph.sh --once        # one iteration (recommended first run)
ralph/ralph.sh --max 10      # up to 10 iterations
ralph/ralph.sh               # loop with defaults (25 iterations)

# No agent installed yet? Prove the harness plumbing (reads prompt + runs verify):
ralph/ralph.sh --dry-run --once

# Use a different agent:
AGENT_CMD="codex exec" ralph/ralph.sh
```

Stop cleanly any time by creating `ralph/STOP` (checked before each iteration) or
pressing Ctrl-C. Per-iteration output is written to `ralph/logs/`.

## Why it works

- **Fresh context each loop** keeps the agent focused and avoids context rot.
- **The filesystem is the memory** — backlog + progress + git, not a chat window.
- **One task per iteration, fully implemented and verified**, yields a clean git
  history with a rollback point at every step.
- **Guardrails** (`AGENT.md`) keep it safe: execution stays gated, no secrets, no
  destructive git, green-before-commit.

## Safety

The harness refuses to run on `main`/`master`. Live on-chain execution is gated
and never enabled implicitly. Read `AGENT.md` before loosening any guardrail.
