# Task template

When decomposing a coarse backlog item, or filing an audit finding, use this
shape. Keep tasks small enough to finish (TDD-first) in one fresh-context loop
iteration.

```
- [ ] **T-XXXX** ⭐(if critical path) <imperative one-line description>
  dep: <ids that must be [x] first, or —>
  tests: <tiers: unit | integration | chain | db | verify | benchmark>

  Context: <1–3 sentences: why, and which doc section governs it,
            e.g. docs/ARBITRAGE_THEORY.md §3.3>
  Acceptance criteria:
    - <observable condition 1 — a test asserts it>
    - <observable condition 2>
    - tree is green (`make check`); relevant tier(s) green
    - docs + progress.md updated; ADR added if an architectural choice was made
```

Rules of thumb:
- One behaviour per task. If acceptance criteria need "and" across unrelated
  behaviours, split it.
- Every task names the **test tiers** it must satisfy; "done" == those green.
- A task that adds a runtime module implicitly requires its paired `test_*`
  (enforced by `check_test_pairing.py`) — you don't need to state that each time.
- Prefer criteria phrased as assertions ("local quote == on-chain quoter within 1
  wei at block N") over vague goals ("pricing works").
```
