# Mode: BUILD (default)

Implement exactly one backlog task. This is the same contract as `ralph/PROMPT.md` — use that as your
authority. In short:

1. Read `ralph/OPERATING_RULES.md`, `CLAUDE.md`, `ralph/MEMORY.md`, `ralph/BACKLOG.md`.
2. If `scripts/verify.sh` is red, fixing it is your task. Otherwise pick the single highest-priority
   unchecked task whose deps are met.
3. Read the spec it cites in `docs/specs/`. Implement a small, tested, spec-faithful change.
4. `bash scripts/verify.sh` → GREEN.
5. Check the task off, journal to `PROGRESS.md`, note gotchas in `MEMORY.md`, commit with the task id.
6. Stop.
