#!/usr/bin/env bash
#
# The verification gate the Ralph loop runs before any commit is considered
# done. Typecheck + tests + build across every workspace package. Exits non-zero
# on the first failure so the loop never commits red.
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

step() { printf '\n\033[1;34m▶ verify: %s\033[0m\n' "$1"; }

step "typecheck"
pnpm -s typecheck

step "test"
pnpm -s test

step "build"
pnpm -s build

printf '\n\033[1;32m✅ verify: all green\033[0m\n'
