#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# ralph/lib/common.sh — shared helpers for the Ralph harness. Sourced by
# loop.sh and run-once.sh. Not meant to be executed directly.
# ---------------------------------------------------------------------------

# Resolve repo root regardless of where the caller invoked from.
RALPH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$RALPH_DIR/.." && pwd)"

# --- Tunables (override via env or .env) -----------------------------------
: "${RALPH_AGENT_CMD:=claude -p --permission-mode acceptEdits}"  # non-interactive agent invocation
: "${RALPH_PROMPT:=$RALPH_DIR/PROMPT.md}"                        # standing prompt fed each iteration
: "${RALPH_MAX_ITERS:=100}"                                      # hard cap on iterations
: "${RALPH_STALL_LIMIT:=3}"                                      # stop after N no-progress iterations
: "${RALPH_SLEEP:=2}"                                            # pause between iterations (seconds)
: "${RALPH_AUTOCOMMIT:=1}"                                       # commit any uncommitted work after an iter
: "${RALPH_VERIFY:=1}"                                           # run scripts/verify.sh after each iter
: "${RALPH_LOG_DIR:=$RALPH_DIR/logs}"
: "${RALPH_STOP_FILE:=$RALPH_DIR/STOP}"

c_reset=$'\033[0m'; c_dim=$'\033[2m'; c_grn=$'\033[32m'; c_red=$'\033[31m'; c_yel=$'\033[33m'; c_cyn=$'\033[36m'
say()  { echo "${c_cyn}[ralph]${c_reset} $*"; }
warn() { echo "${c_yel}[ralph] warn:${c_reset} $*" >&2; }
die()  { echo "${c_red}[ralph] fatal:${c_reset} $*" >&2; exit 1; }

# Load .env if present so RPCs/keys/tunables are available to the agent + tools.
load_env() {
  if [[ -f "$REPO_ROOT/.env" ]]; then
    set -a; # shellcheck disable=SC1091
    source "$REPO_ROOT/.env"; set +a
  fi
}

# A cheap fingerprint of the working tree + HEAD, used to detect "did anything change?".
tree_fingerprint() {
  git -C "$REPO_ROOT" add -A -n >/dev/null 2>&1 || true
  { git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null; \
    git -C "$REPO_ROOT" status --porcelain 2>/dev/null; \
    git -C "$REPO_ROOT" diff --stat 2>/dev/null; } | sha1sum | awk '{print $1}'
}

# Count remaining unchecked backlog items ("- [ ]") — used to detect completion.
open_backlog_items() {
  grep -cE '^\s*-\s*\[\s\]' "$RALPH_DIR/BACKLOG.md" 2>/dev/null || echo 0
}

# Feed the standing prompt to the agent. Override the whole invocation with
# RALPH_AGENT_CMD (it receives the prompt on stdin and as the -p argument).
run_agent() {
  local prompt_file="$1" logfile="$2"
  say "invoking agent: ${c_dim}${RALPH_AGENT_CMD}${c_reset}"
  # shellcheck disable=SC2086
  cat "$prompt_file" | $RALPH_AGENT_CMD 2>&1 | tee "$logfile"
  return "${PIPESTATUS[1]}"
}

# Commit whatever the agent left uncommitted, so every iteration is a checkpoint.
autocommit() {
  local n="$1"
  git -C "$REPO_ROOT" add -A
  if ! git -C "$REPO_ROOT" diff --cached --quiet; then
    git -C "$REPO_ROOT" commit -q -m "chore(ralph): checkpoint iteration ${n}" \
      -m "Automated checkpoint by ralph/loop.sh. See ralph/PROGRESS.md." || true
    say "checkpoint committed"
    return 0
  fi
  return 1
}
