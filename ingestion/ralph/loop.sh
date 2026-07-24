#!/usr/bin/env bash
#
# Ralph loop runner — repeatedly feeds ralph/PROMPT.md to the coding agent,
# each iteration in a FRESH context, until the build declares itself complete.
#
# The technique (Geoffrey Huntley's "Ralph"): a single fixed prompt + all state
# on disk (PROGRESS.md, code, tests) lets a stateless agent build a whole system
# across many small, green, committed steps. See ralph/PROMPT.md + ralph/AGENTS.md.
#
# Usage:
#   ./ralph/loop.sh                 # loop until RALPH-COMPLETE, from repo root
#   MAX_ITER=20 ./ralph/loop.sh     # cap iterations (0 = unlimited, default)
#   LOOP_DELAY=10 ./ralph/loop.sh   # seconds between iterations (default 5)
#   AGENT_CMD="claude -p --dangerously-skip-permissions --model claude-opus-4-8" ./ralph/loop.sh
#
# Notes:
#   * Run from the repository root.
#   * The agent needs permission to edit files, run cargo, and git-commit.
#   * Each iteration is logged to ralph/logs/iter-<n>.log.
#   * The loop stops when ralph/PROGRESS.md contains the RALPH-COMPLETE sentinel,
#     when MAX_ITER is reached, or on Ctrl-C.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT" || exit 1

PROMPT_FILE="ralph/PROMPT.md"
PROGRESS_FILE="ralph/PROGRESS.md"
LOG_DIR="ralph/logs"
SENTINEL="RALPH-COMPLETE"

MAX_ITER="${MAX_ITER:-0}"          # 0 = unlimited
LOOP_DELAY="${LOOP_DELAY:-5}"
AGENT_CMD="${AGENT_CMD:-claude -p --dangerously-skip-permissions --model claude-opus-4-8}"

mkdir -p "$LOG_DIR"

if [[ ! -f "$PROMPT_FILE" ]]; then
  echo "FATAL: $PROMPT_FILE not found. Run from the repo root." >&2
  exit 1
fi

echo "Ralph loop starting in $REPO_ROOT"
echo "  agent:      $AGENT_CMD"
echo "  max iters:  $([[ "$MAX_ITER" -eq 0 ]] && echo unlimited || echo "$MAX_ITER")"
echo "  delay:      ${LOOP_DELAY}s"
echo

iter=0
while :; do
  # Stop if a previous iteration already declared completion.
  if [[ -f "$PROGRESS_FILE" ]] && grep -q "$SENTINEL" "$PROGRESS_FILE"; then
    echo "✅ $SENTINEL found in $PROGRESS_FILE — build complete after $iter iteration(s)."
    break
  fi

  iter=$((iter + 1))
  ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  log="$LOG_DIR/iter-$(printf '%04d' "$iter").log"

  echo "═══════════════════════════════════════════════════════════════"
  echo "  Ralph iteration $iter · $ts"
  echo "  log: $log"
  echo "═══════════════════════════════════════════════════════════════"

  # Feed the fixed prompt to a fresh agent context. Tee output for later audit.
  if ! $AGENT_CMD < "$PROMPT_FILE" 2>&1 | tee "$log"; then
    echo "⚠️  agent exited non-zero on iteration $iter (see $log). Continuing after delay." >&2
  fi

  if [[ "$MAX_ITER" -gt 0 && "$iter" -ge "$MAX_ITER" ]]; then
    echo "Reached MAX_ITER=$MAX_ITER. Stopping (build may be incomplete — check $PROGRESS_FILE)."
    break
  fi

  sleep "$LOOP_DELAY"
done
