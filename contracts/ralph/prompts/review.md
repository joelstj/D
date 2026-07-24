# Mode: ADVERSARIAL REVIEW

Attack the most recent work. Assume it is subtly wrong and try to prove it.

1. `git log --oneline -15` and `git diff HEAD~5..HEAD` (adjust range). Focus on the newest contracts.
2. Hunt specifically for:
   - **Profit-invariant holes** — any path that can end with the vault holding less than it started.
   - **Callback trust bugs** — flash-loan/swap callbacks not asserting both `msg.sender == pool` and
     `initiator == address(this)`.
   - **Reentrancy** — external calls before state settle; guard gaps; transient-storage guard misuse.
   - **Rounding / overflow / unchecked** — sizing and swap math; fee-on-transfer and rebasing tokens;
     decimals mismatches; `unchecked` blocks without a proof comment.
   - **Approval hygiene** — leftover or unbounded allowances.
   - **Gas regressions** — compare `.gas-snapshot`; flag hot-path growth.
3. For each real finding: add a failing test that demonstrates it (best), or a `- [ ]` backlog task
   with a repro sketch. Fix it now only if the fix is small and obviously correct; otherwise queue it.
4. `bash scripts/verify.sh` → GREEN. Journal findings in `PROGRESS.md`. Commit. Stop.
