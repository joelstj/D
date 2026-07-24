#!/usr/bin/env bash
# =============================================================================
# ralph/loop.sh — the Ralph build loop for l2arb.
#
# Repeatedly runs Claude Code with a FRESH context on ralph/PROMPT.md. Each
# iteration reads the plan from disk, does exactly one backlog task TDD-first,
# leaves the tree green, records progress, commits, and stops. The loop then
# restarts it for the next task. Filesystem = memory.
#
#   Usage:   make loop            # or:  bash ralph/loop.sh
#
#   Stop:    touch ralph/STOP     # graceful stop after the current iteration
#   Done:    the agent writes ralph/DONE when the backlog is exhausted
#
# ⚠️  AUTONOMY / SAFETY
#   For unattended operation the agent must not block on permission prompts, so
#   this defaults to Claude Code's bypass-permissions mode. Run it ONLY in an
#   isolated/sandboxed environment (like this remote container) where it cannot
#   touch anything you care about outside the repo. To run attended/safer, set
#   RALPH_PERMISSION_MODE=acceptEdits (you will be prompted for bash/other tools).
#   The engine itself never holds keys and never trades (see CLAUDE.md §1) — this
#   warning is about the build agent's filesystem/tool access, not on-chain risk.
# =============================================================================
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1
ROOT="$(pwd)"

# ---- config (override via env) ----------------------------------------------
BRANCH="${RALPH_BRANCH:-claude/l2-arbitrage-engine-j4olzf}"
PROMPT_FILE="${RALPH_PROMPT:-ralph/PROMPT.md}"
MAX_ITERS="${RALPH_MAX_ITERS:-500}"        # hard cap so a bug can't loop forever
COOLDOWN="${RALPH_COOLDOWN:-3}"            # seconds between iterations
PERMISSION_MODE="${RALPH_PERMISSION_MODE:-bypassPermissions}"
MODEL="${RALPH_MODEL:-}"                    # optional: pin a model id
GREEN_GATE="${RALPH_GREEN_GATE:-1}"        # 1 = verify tree green between iters
LOG_DIR="${RALPH_LOG_DIR:-ralph/logs}"

mkdir -p "$LOG_DIR"

log() { printf '\033[36m[ralph %s]\033[0m %s\n' "$(iso_now)" "$*"; }
iso_now() { date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo "?"; }

# ---- preflight --------------------------------------------------------------
command -v claude >/dev/null 2>&1 || { echo "claude CLI not found on PATH"; exit 1; }
[[ -f "$PROMPT_FILE" ]] || { echo "prompt file missing: $PROMPT_FILE"; exit 1; }

git rev-parse --abbrev-ref HEAD | grep -qx "$BRANCH" || {
  log "checking out $BRANCH"
  git checkout -B "$BRANCH" || exit 1
}

log "starting loop: max=$MAX_ITERS mode=$PERMISSION_MODE branch=$BRANCH"
log "stop anytime with:  touch ralph/STOP"
[[ "$PERMISSION_MODE" == "bypassPermissions" ]] && \
  log "⚠️  bypass-permissions mode — ensure this is an isolated environment"

CLAUDE_ARGS=(-p --permission-mode "$PERMISSION_MODE" --verbose)
[[ -n "$MODEL" ]] && CLAUDE_ARGS+=(--model "$MODEL")

# ---- the loop ---------------------------------------------------------------
for ((i = 1; i <= MAX_ITERS; i++)); do
  if [[ -f ralph/STOP ]]; then log "STOP sentinel found — exiting"; rm -f ralph/STOP; break; fi
  if [[ -f ralph/DONE ]]; then log "DONE sentinel found — backlog complete"; break; fi

  iter_log="$LOG_DIR/iter-$(printf '%04d' "$i").log"
  log "iteration $i/$MAX_ITERS  → $iter_log"

  # Run one fresh-context iteration. Prompt on stdin; transcript teed to a log.
  if ! claude "${CLAUDE_ARGS[@]}" < "$PROMPT_FILE" 2>&1 | tee "$iter_log"; then
    log "iteration $i: claude exited non-zero (see $iter_log) — continuing"
  fi

  # Between-iteration safety net: never let a red tree persist silently.
  if [[ "$GREEN_GATE" == "1" ]]; then
    if make check >/dev/null 2>&1; then
      log "iteration $i: tree is GREEN"
    else
      log "iteration $i: tree is RED after iteration — next iteration must fix it"
    fi
  fi

  sleep "$COOLDOWN"
done

log "loop finished after $((i > MAX_ITERS ? MAX_ITERS : i)) iteration(s)"
[[ -f ralph/DONE ]] && { log "ralph/DONE says:"; cat ralph/DONE; }
