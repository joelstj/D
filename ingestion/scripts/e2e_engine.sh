#!/usr/bin/env bash
#
# Tier-B live engine e2e — drive the **real** `l2arb` detection engine through the
# real `EngineClient`, end-to-end (closes the M8 "real-l2arb net_profit>0" gate that
# was BLOCKED while the engine was not co-located; see `ralph/PROGRESS.md`).
#
# `contract_test.rs` proves client conformance against a wiremock/subprocess stand-in
# (Tier-A, always runs). This harness runs `tests/live_engine.rs` against the REAL
# engine: it detects a known arbitrage and returns a contract-valid net_profit>0
# response, checked by `validate_response` (INTEGRATION.md §10).
#
# In the combined workspace the engine is a sibling repo, so this "just works":
#
#   ./scripts/e2e_engine.sh                                   # subprocess transport (default)
#   L2ARB_ENGINE_DIR=/path/to/Python-Engine-L2-s ./scripts/e2e_engine.sh
#   L2ARB_ENGINE_URL=http://127.0.0.1:8080 ./scripts/e2e_engine.sh   # HTTP transport
#
# The engine must have its deps installed once (`cd Python-Engine-L2-s && uv sync`).

set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

# HTTP transport wins if a URL is provided; otherwise use the subprocess runner.
if [[ -n "${L2ARB_ENGINE_URL:-}" ]]; then
  echo "==> engine transport: HTTP ($L2ARB_ENGINE_URL)"
  export L2ARB_ENGINE_URL
else
  ENGINE_DIR="${L2ARB_ENGINE_DIR:-$(cd .. && pwd)/Python-Engine-L2-s}"
  if [[ ! -d "$ENGINE_DIR" ]]; then
    echo "ERROR: engine dir not found: $ENGINE_DIR" >&2
    echo "       set L2ARB_ENGINE_DIR or L2ARB_ENGINE_URL (see script header)." >&2
    exit 2
  fi
  export L2ARB_ENGINE_CMD="uv run --directory ${ENGINE_DIR} python -m l2arb.api.runner"
  echo "==> engine transport: subprocess ($L2ARB_ENGINE_CMD)"
fi

echo "==> running live engine e2e (tests/live_engine.rs)"
cargo test -p l2i-engine-client --test live_engine -- --nocapture
