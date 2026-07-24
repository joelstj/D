#!/usr/bin/env bash
#
# Ralph loop — point a coding agent at ralph/PROMPT.md and run it repeatedly with
# a fresh context each iteration. State survives between runs through the repo,
# ralph/backlog.md, ralph/progress.md, and git history.
#
#   ralph/ralph.sh                 # loop with defaults
#   ralph/ralph.sh --once          # a single iteration
#   ralph/ralph.sh --max 10        # cap iterations
#   ralph/ralph.sh --dry-run       # exercise the harness without invoking an agent
#   AGENT_CMD="codex exec" ralph/ralph.sh   # use a different agent
#
# Stop cleanly at any time by creating the file ralph/STOP (the loop checks it
# before each iteration) or with Ctrl-C.
#
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# --- configuration (env-overridable) ---------------------------------------
AGENT_CMD="${AGENT_CMD:-claude -p --dangerously-skip-permissions}"
MAX_ITERATIONS="${MAX_ITERATIONS:-25}"
SLEEP_SECONDS="${SLEEP_SECONDS:-5}"
PROMPT_FILE="${PROMPT_FILE:-ralph/PROMPT.md}"
DRY_RUN=0

while [ $# -gt 0 ]; do
  case "$1" in
    --once) MAX_ITERATIONS=1 ;;
    --max) MAX_ITERATIONS="$2"; shift ;;
    --sleep) SLEEP_SECONDS="$2"; shift ;;
    --agent) AGENT_CMD="$2"; shift ;;
    --dry-run) DRY_RUN=1 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
  shift
done

LOG_DIR="ralph/logs"
STATE_DIR="ralph/.state"
STOP_FILE="ralph/STOP"
mkdir -p "$LOG_DIR" "$STATE_DIR"

log() { printf '\033[1;35m[ralph]\033[0m %s\n' "$*"; }

cleanup() { log "interrupted — exiting"; exit 130; }
trap cleanup INT TERM

# --- preflight --------------------------------------------------------------
BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')"
if [ "$BRANCH" = "main" ] || [ "$BRANCH" = "master" ]; then
  log "refusing to run on '$BRANCH' — check out a feature branch first."
  exit 1
fi
log "branch: $BRANCH | agent: ${AGENT_CMD} | max: ${MAX_ITERATIONS} | dry-run: ${DRY_RUN}"

if [ "$DRY_RUN" = "0" ] && ! command -v "${AGENT_CMD%% *}" >/dev/null 2>&1; then
  log "agent command '${AGENT_CMD%% *}' not found on PATH."
  log "install it, or pass --agent \"<cmd>\", or use --dry-run to test the harness."
  exit 127
fi

remaining_tasks() { grep -c '^- \[ \]' ralph/backlog.md 2>/dev/null || echo 0; }

# --- loop -------------------------------------------------------------------
for i in $(seq 1 "$MAX_ITERATIONS"); do
  if [ -f "$STOP_FILE" ]; then
    log "STOP file present — halting. Remove ralph/STOP to resume."
    break
  fi

  left="$(remaining_tasks)"
  if [ "$left" -eq 0 ]; then
    log "backlog has no open [ ] tasks — nothing to do. Add items to ralph/backlog.md."
    break
  fi

  stamp="$(date +%Y%m%d-%H%M%S)"
  iter_log="$LOG_DIR/iter-$stamp.log"
  log "iteration $i/$MAX_ITERATIONS — $left task(s) open — logging to $iter_log"

  if [ "$DRY_RUN" = "1" ]; then
    {
      echo "=== DRY RUN — prompt that would be sent to: $AGENT_CMD ==="
      cat "$PROMPT_FILE"
      echo
      echo "=== running verify gate to prove the harness plumbing ==="
    } | tee "$iter_log"
    if bash ralph/verify.sh >>"$iter_log" 2>&1; then
      log "dry-run verify: PASS"
    else
      log "dry-run verify: FAIL (see $iter_log)"
    fi
  else
    # The heart of Ralph: feed the same prompt to a fresh agent context.
    if ! cat "$PROMPT_FILE" | eval "$AGENT_CMD" 2>&1 | tee "$iter_log"; then
      log "agent invocation returned non-zero (see $iter_log) — continuing."
    fi
  fi

  echo "$stamp" > "$STATE_DIR/last-iteration"
  [ "$i" -lt "$MAX_ITERATIONS" ] && sleep "$SLEEP_SECONDS"
done

log "done. progress: ralph/progress.md | remaining: $(remaining_tasks) task(s)."
