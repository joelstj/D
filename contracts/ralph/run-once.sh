#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# ralph/run-once.sh — run a single Ralph iteration and stop. Handy for
# watching exactly what the agent does, or for driving the build one careful
# step at a time from CI or a human-in-the-loop workflow.
#
#   bash ralph/run-once.sh                 # one iteration, then verify + checkpoint
#   RALPH_PROMPT=ralph/prompts/review.md bash ralph/run-once.sh   # run a specific mode prompt
# ---------------------------------------------------------------------------
set -uo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

mkdir -p "$RALPH_LOG_DIR"
load_env
[[ -f "$RALPH_PROMPT" ]] || die "prompt not found: $RALPH_PROMPT"

ts="$(date -u +%Y%m%dT%H%M%SZ 2>/dev/null || echo once)"
log="$RALPH_LOG_DIR/once-${ts}.log"

say "single iteration → prompt: ${RALPH_PROMPT}"
run_agent "$RALPH_PROMPT" "$log" || warn "agent exited non-zero (see $log)"

if [[ "$RALPH_VERIFY" == "1" && -x "$REPO_ROOT/scripts/verify.sh" ]]; then
  bash "$REPO_ROOT/scripts/verify.sh" || warn "verify RED — fix before committing."
fi

[[ "$RALPH_AUTOCOMMIT" == "1" ]] && autocommit "once" || true
say "done. log: $log"
