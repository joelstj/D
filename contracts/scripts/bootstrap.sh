#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# bootstrap.sh — install and pin the toolchain this repo needs. Idempotent:
# safe to run repeatedly; fast-paths when tools are already present. Optional
# installs (Slither, node deps) are best-effort and never fail the script.
#
#   bash scripts/bootstrap.sh            # full, verbose
#   bash scripts/bootstrap.sh --quiet    # quiet fast-path (used by SessionStart hook)
# ---------------------------------------------------------------------------
set -uo pipefail

QUIET=0
[[ "${1:-}" == "--quiet" ]] && QUIET=1
log() { [[ $QUIET -eq 1 ]] || echo "[bootstrap] $*"; }
have() { command -v "$1" >/dev/null 2>&1; }

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Make a Foundry install visible for the rest of this script even before login shells reload PATH.
export PATH="$HOME/.foundry/bin:$PATH"

# --- 1. Foundry (forge/cast/anvil) -----------------------------------------
if have forge; then
  log "forge present: $(forge --version 2>/dev/null | head -1)"
else
  log "installing Foundry ..."
  if curl -fsSL https://foundry.paradigm.xyz | bash >/dev/null 2>&1; then
    # Pin to the same version CI uses (.github/workflows/ci.yml) — `forge fmt`
    # output is version-sensitive, so a floating/newer install here reformats
    # otherwise-unchanged code and fails `forge fmt --check` in CI.
    "$HOME/.foundry/bin/foundryup" --install 1.5.1 >/dev/null 2>&1 || log "WARN: foundryup failed (network policy?). Install manually: https://book.getfoundry.sh/getting-started/installation"
  else
    log "WARN: could not download Foundry installer (offline / restricted network)."
    log "      Install manually later; the rest of bootstrap will continue."
  fi
fi

# --- 2. Solidity libraries (vendored under lib/ via forge) ------------------
install_lib() {
  local name="$1" repo="$2" tag="$3" dir="lib/$1"
  [[ -d "$dir" && -n "$(ls -A "$dir" 2>/dev/null)" ]] && { log "lib/$name present"; return 0; }
  have forge || { log "skip lib/$name (forge missing)"; return 0; }
  log "installing $name@$tag ..."
  forge install "$repo@$tag" >/dev/null 2>&1 \
    || forge install "$repo" >/dev/null 2>&1 \
    || log "WARN: forge install $repo failed (network?). Retry after Foundry is available."
}
if have forge; then
  # Pinned, well-known dependencies. Tags can be bumped in a dedicated task.
  install_lib "forge-std"             "foundry-rs/forge-std"            "v1.9.4"
  install_lib "openzeppelin-contracts" "OpenZeppelin/openzeppelin-contracts" "v5.1.0"
  install_lib "solmate"               "transmissions11/solmate"        "v7"
  install_lib "permit2"               "Uniswap/permit2"                "main"
fi

# --- 3. Node workspace deps (off-chain + TS SDK) — best effort -------------
if have npm && [[ -f package.json ]]; then
  if [[ ! -d node_modules ]]; then
    log "installing node deps ..."
    npm install --no-audit --no-fund >/dev/null 2>&1 || log "WARN: npm install failed (ok for now)."
  else
    log "node_modules present"
  fi
fi

# --- 4. Slither static analyzer (optional) ---------------------------------
if have slither; then
  log "slither present"
elif have pip3; then
  log "installing slither (optional) ..."
  pip3 install --quiet slither-analyzer >/dev/null 2>&1 || log "WARN: slither install skipped."
fi

# --- 5. Local env file ------------------------------------------------------
[[ -f .env || ! -f .env.example ]] || { cp .env.example .env; log "created .env from .env.example (fill it in)"; }

log "done."
if [[ $QUIET -eq 0 ]]; then
  echo
  echo "Next: bash scripts/verify.sh   # confirm the baseline is green"
fi
