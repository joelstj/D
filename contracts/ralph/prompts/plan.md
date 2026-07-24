# Mode: PLAN / GROOM (no code)

Do not write contract code this iteration. Groom the plan so future build iterations are unambiguous.

1. Read `docs/BUILD_PLAN.md` and skim `docs/specs/`. Read `ralph/BACKLOG.md` and `ralph/PROGRESS.md`.
2. Reconcile the backlog with reality:
   - Split any task that is too big to finish (and verify) in a single iteration.
   - Add dependencies you can see are missing. Reorder so blocked work sinks below ready work.
   - Mark tasks blocked on a human decision with `(BLOCKED: …)` and mirror the question in
     `PROGRESS.md` under `### NEEDS HUMAN`.
   - Add tasks for gaps you notice (missing tests, specs, adapters, chains).
3. Keep task IDs stable (`P{phase}-T{n}`). Never silently delete a task — check it off or annotate why.
4. Update `docs/BUILD_PLAN.md` if the phase structure genuinely changed. Keep BACKLOG and BUILD_PLAN
   consistent (same phase numbers, same task ids).
5. Journal what you changed in `PROGRESS.md`. Commit `chore(plan): groom backlog`. Stop.
