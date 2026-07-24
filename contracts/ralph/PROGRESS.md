# Ralph progress journal

Append-only. Newest entry at the top. One entry per iteration. Format:

```
## <UTC date> — <task id> — <one-line summary>
- did: <what changed>
- verify: GREEN | RED (why)
- next: <hint for the next iteration>
```

Use a `### NEEDS HUMAN` block for anything requiring a human decision (capital limits, risk tolerance,
provider prioritization, enabling a live deploy). The loop will surface it.

---

## 2026-07-16 — bootstrap — harness + build plan scaffolded
- did: Created the Ralph loop harness (`ralph/`), the phased build plan (`docs/BUILD_PLAN.md`), the
  deep specs (`docs/specs/`), per-chain config scaffolding (`config/chains/`), and a compiling contract
  skeleton (`src/`, `test/`). This is Phase 0's starting point.
- verify (partial, in this bootstrap env):
  - `config/chains/*.json` validated against `config/chains/schema.json` (jsonschema draft 2020-12): ALL VALID.
  - All shell scripts pass `bash -n`; `package.json` + `.claude/settings.json` are valid JSON.
  - `src/**/*.sol` compiled with **solc 0.8.24, via-IR, evm_version=cancun, optimizer** → **0 errors**
    (ArbExecutor ~2.8KB). Two EXPECTED warnings remain — `_lock = 1` unreachable and the unused
    `profit` return — both because the skeleton `execute()` always `revert NotImplemented()`; they
    vanish when P2 gives `execute` a real (non-reverting) path. `forge build` does not fail on warnings.
  - Foundry (`forge`) and `forge-std` could NOT be installed here: the GitHub release/API host is
    blocked by this environment's egress policy (403 via the agent proxy). So `forge fmt/build/test`
    was not run end-to-end. `test/ArbExecutor.t.sol` uses only standard forge-std cheatcodes.
- next: **P0-T1** — run `bash scripts/bootstrap.sh` in an env with GitHub egress (installs Foundry +
  forge-std/OZ), then `bash scripts/verify.sh`; run `forge fmt` once to normalize formatting and repair
  anything red. Then proceed in order through `ralph/BACKLOG.md`. Do not skip ahead — Phase 0
  establishes the green baseline + CI that every later task depends on.

### NEEDS HUMAN
- Capital & risk limits are unset: max notional per trade, min profit threshold (bps and absolute),
  per-chain gas ceiling, and which flash-loan provider to prefer where several exist. These gate
  Phases 3, 6, and 7. Defaults are proposed in `config/strategies.example.json`; confirm or override.
- Live deployment is intentionally disabled in the harness. A human must explicitly enable and run any
  broadcast (`script/` + `--broadcast`), ideally only after an external audit (see `docs/specs/08`).
