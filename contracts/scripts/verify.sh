#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# verify.sh — the single "definition of done" gate. Every Ralph task and CI
# run must pass this before a task is checked off. Fails fast, prints a clear
# summary, and returns non-zero if anything is red.
#
#   bash scripts/verify.sh            # fmt check + build + test (+ slither if present)
#   VERIFY_SKIP_FMT=1 bash ...        # skip the format check
#   VERIFY_GAS=1 bash ...             # also write a gas snapshot
#   VERIFY_FORK=1 bash ...            # also run fork tests (requires *_RPC_URL in env)
# ---------------------------------------------------------------------------
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PATH="$HOME/.foundry/bin:$PATH"

fail=0
step() { echo; echo "▶ $*"; }
ok()   { echo "  ✅ $*"; }
bad()  { echo "  ❌ $*"; fail=1; }

if ! command -v forge >/dev/null 2>&1; then
  echo "❌ forge not found. Run: bash scripts/bootstrap.sh"
  exit 127
fi

# 1. Formatting -------------------------------------------------------------
if [[ "${VERIFY_SKIP_FMT:-0}" != "1" ]]; then
  step "forge fmt --check"
  if forge fmt --check; then ok "formatting clean"; else bad "run 'forge fmt' to fix formatting"; fi
fi

# 2. Compile ----------------------------------------------------------------
step "forge build"
if forge build; then ok "contracts compile"; else bad "compilation failed"; fi

# 3. Tests ------------------------------------------------------------------
step "forge test"
TEST_ARGS=("-vvv")
[[ "${VERIFY_FORK:-0}" == "1" ]] || TEST_ARGS+=("--no-match-path" "test/fork/**")
if forge test "${TEST_ARGS[@]}"; then ok "tests pass"; else bad "tests failed"; fi

# 4. Gas snapshot (optional, non-blocking) ----------------------------------
if [[ "${VERIFY_GAS:-0}" == "1" ]]; then
  step "forge snapshot"
  forge snapshot && ok "gas snapshot written (.gas-snapshot)" || echo "  (snapshot skipped)"
fi

# 5. Static analysis (optional, non-blocking on tool absence) ---------------
if command -v slither >/dev/null 2>&1; then
  step "slither ."
  # Slither's own findings are advisory here; a crash is not a hard fail so the
  # loop keeps moving. Treat HIGH findings as review items via docs/specs/08.
  slither . --fail-none || echo "  (slither reported findings — review them)"
fi

echo
if [[ $fail -eq 0 ]]; then
  echo "✅ verify: GREEN"
else
  echo "❌ verify: RED — fix the items above before checking a task off."
fi
exit $fail
