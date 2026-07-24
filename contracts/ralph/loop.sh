#!/usr/bin/env bash
# ===========================================================================
#  ralph/loop.sh — the autonomous build loop ("Ralph").
#
#  Repeatedly feeds ralph/PROMPT.md to a coding agent. Each iteration the agent
#  picks ONE task from ralph/BACKLOG.md, implements it, runs scripts/verify.sh,
#  checks it off, journals progress, and commits. The loop then restarts the
#  agent with a fresh context — the filesystem (BACKLOG/PROGRESS/MEMORY) is the
#  memory that carries state across iterations.
#
#  Stops when: the STOP sentinel appears, the iteration cap is hit, the backlog
#  has no open items, or progress stalls for RALPH_STALL_LIMIT iterations.
#
#  Usage:
#     bash ralph/loop.sh
#     RALPH_MAX_ITERS=25 RALPH_AGENT_CMD="claude -p --dangerously-skip-permissions" bash ralph/loop.sh
#     touch ralph/STOP        # ask the loop to stop gracefully after the current iteration
# ===========================================================================
set -uo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

mkdir -p "$RALPH_LOG_DIR"
load_env
rm -f "$RALPH_STOP_FILE"

command -v git >/dev/null 2>&1 || die "git is required"
[[ -f "$RALPH_PROMPT" ]] || die "prompt not found: $RALPH_PROMPT"

trap 'echo; say "interrupted — stopping after current step"; touch "$RALPH_STOP_FILE"' INT

say "starting loop  ${c_dim}(max=${RALPH_MAX_ITERS}, stall_limit=${RALPH_STALL_LIMIT}, verify=${RALPH_VERIFY})${c_reset}"
say "agent command : ${RALPH_AGENT_CMD}"
say "stop any time : touch ${RALPH_STOP_FILE}"

iter=0
stall=0
prev_fp="$(tree_fingerprint)"

while (( iter < RALPH_MAX_ITERS )); do
  [[ -f "$RALPH_STOP_FILE" ]] && { say "STOP sentinel found — halting."; break; }

  open="$(open_backlog_items)"
  if [[ "$open" == "0" ]]; then
    say "${c_grn}BACKLOG has no open items — build complete.${c_reset}"
    break
  fi

  iter=$(( iter + 1 ))
  ts="$(date -u +%Y%m%dT%H%M%SZ 2>/dev/null || echo iter)"
  log="$RALPH_LOG_DIR/iter-$(printf '%03d' "$iter")-${ts}.log"
  echo
  say "──────── iteration ${iter}/${RALPH_MAX_ITERS}  ${c_dim}(${open} backlog items open)${c_reset} ────────"

  run_agent "$RALPH_PROMPT" "$log" || warn "agent exited non-zero (see $log) — continuing"

  # Double-check the tree is green; the agent is expected to have done this already.
  if [[ "$RALPH_VERIFY" == "1" && -x "$REPO_ROOT/scripts/verify.sh" ]]; then
    if bash "$REPO_ROOT/scripts/verify.sh" >>"$log" 2>&1; then
      say "verify: ${c_grn}GREEN${c_reset}"
    else
      warn "verify: ${c_red}RED${c_reset} after iteration ${iter} — the next iteration must fix this first."
    fi
  fi

  [[ "$RALPH_AUTOCOMMIT" == "1" ]] && autocommit "$iter" || true

  # Stall detection: did anything about the repo change this iteration?
  fp="$(tree_fingerprint)"
  if [[ "$fp" == "$prev_fp" ]]; then
    stall=$(( stall + 1 ))
    warn "no changes detected (${stall}/${RALPH_STALL_LIMIT} stalls)"
    if (( stall >= RALPH_STALL_LIMIT )); then
      say "${c_yel}stall limit reached — halting. Inspect $log and ralph/PROGRESS.md.${c_reset}"
      break
    fi
  else
    stall=0
  fi
  prev_fp="$fp"

  sleep "$RALPH_SLEEP"
done

echo
say "loop finished after ${iter} iteration(s). Open backlog items: $(open_backlog_items)."
say "logs: ${RALPH_LOG_DIR}   progress: ralph/PROGRESS.md"
