# Testing Strategy

> The brief: *"a test-based development workflow that creates and applies testing
> suites to every couple of files created that tests everything: syntax, logic,
> integration, operations, features, functions, databases, latency … until
> everything is verified passing 100% and can be verified current, correct, and
> only uses real live blockchain data."* This document turns that into concrete,
> enforced mechanics.

## 1. TDD is the workflow, not a phase

Each backlog task follows **red → green → refactor**:
1. Write the test(s) for the behaviour and the tier(s) it belongs to.
2. Run them; confirm they fail for the expected reason (a never-red test is
   worthless).
3. Implement the minimum elegant code to pass.
4. Refactor with the tests as a safety net.
5. Run the full fast gate (`make check`) — green — then commit.

"Every couple of files" is enforced structurally by **test pairing** (§4) and by
the rule in `CLAUDE.md` §4: after roughly every two new source files, run the
*integration* tier for that subsystem, because wiring bugs live between green
units.

## 2. The tiers ("tests everything")

| Tier | Marker | Answers | Tools |
|------|--------|---------|-------|
| **Syntax / style** | (lint) | Does it parse & meet style? | `ruff format --check`, `ruff check` |
| **Types / logic** | (mypy) | Are interfaces & units sound? | `mypy --strict` |
| **Unit / functions** | `unit` | Is each function correct? | `pytest`, `hypothesis` |
| **Features** | `unit`/`integration` | Does a use-case work end-to-end? | `pytest` |
| **Integration** | `integration` | Do boundaries cooperate? | `pytest` + local services |
| **Chain** | `chain` | Does it match real chain state? | Anvil fork / live RPC |
| **Databases** | `db` | Do schema & queries round-trip? | Postgres/Timescale + Redis |
| **Verify / data-integrity** | `verify` | Is data on-chain-verifiable & fresh? | independent oracle |
| **Operations** | `integration` | Reconnect, failover, reorg, chaos? | fault-injection fixtures |
| **Latency** | `benchmark` | Are SLOs met? | `pytest-benchmark` |
| **Security** | (audit) | Deps & code safe, no secrets? | `pip-audit`, `bandit`, secret scan |

Markers are declared in `pyproject.toml`; `--strict-markers` fails on typos.

## 3. What "100% passing" means (and doesn't)

- **Pass rate: 100%.** Zero failing, zero unexplained skips, zero `xfail` used to
  hide a real regression. A skip is legitimate only when a resource is absent
  (e.g. `chain` tests skip when no RPC configured) and it is logged as skipped,
  never counted as passed.
- **Coverage: ratcheting, not blindly 100%.** Chasing 100% line coverage
  everywhere incentivizes shallow tests. Instead:
  - Global floor `COV_FAIL_UNDER` (starts at 85) **only ever rises**.
  - **`amm/` and `detect/` target 100% line + branch** — this is the code where a
    bug silently loses money; it is held to the strict bar in CI.
  - Coverage is measured with `--cov-branch`; a task that lowers coverage fails
    the gate.

## 4. Test pairing (the "every file is tested" guarantee)

`scripts/check_test_pairing.py` fails the build if any `src/l2arb/**/*.py` module
(excluding `__init__.py`, pure `Protocol` port files, and an allowlist) lacks a
corresponding `tests/**/test_<module>.py`. This makes "untested file" a build
error, not a code-review afterthought. Run in pre-commit and CI.

## 5. Correctness techniques

- **Property-based tests (`hypothesis`)** for all AMM math and graph identities:
  - constant-product: `x'·y' ≥ x·y`; monotonic out-given-in; round-trip loses
    only fee; in-given-out ∘ out-given-in ≈ identity within rounding.
  - graph: `Σ −ln r < 0 ⇔ Π r > 1`.
  - profit gate: never reports a net-loss cycle over random valid pool states.
- **Golden vectors**: hand-computed AMM results and captured real on-chain quotes
  at pinned blocks, asserted exactly.
- **Oracle cross-check (`verify`) — realized bit-for-bit.** The engine reproduces
  a live Base Uniswap **V2** WETH/USDC pool (`Router.getAmountsOut`, 5 sizes/both
  directions) and a **V3** WETH/USDC pool (`QuoterV2`, both directions incl. the
  post-swap `sqrtPriceX96`) at a pinned block, **to the wei** — fed through the
  real `pool_from_dict` ingestion boundary (`tests/verify/test_onchain_amm.py`,
  fixtures in `tests/verify/fixtures/`). Frozen for deterministic offline replay,
  so it runs in the normal gate as a permanent regression guard.
- **Adversarial / stress tier** (`tests/stress/`): property tests hammer every AMM
  family at the edges (uint112 ceiling, 1-wei dust, ~100% fee, V3 at the protocol
  price bounds, pathological amp/weights) — output stays bounded, monotonic, and
  never drains the pool; nothing raises. A whole-engine test over a graph salted
  with degenerate pools asserts **every reported opportunity is provably
  net-profitable** (the cardinal sin is a phantom edge), and a ~64-token scale
  graph checks soundness + ranking + determinism + `incremental ≡ full`. This tier
  found and pinned a real Balancer over-statement bug (§ `amm/weighted`).
- **Equivalence tests**: incremental (dirty-set) detection ≡ full-graph detection
  on the same state.
- **No-false-positive tests**: arbitrage-free synthetic graphs must yield nothing.

## 6. Integration, database & operations tiers

- **RPC-fork tests** run against a local **Anvil** fork pinned to a block, so
  results are deterministic and free. Fixture spins up/stops the fork.
- **DB tests** run against ephemeral Postgres/Timescale + Redis (`make
  services-up`, or Testcontainers in CI): migrations apply cleanly, snapshots and
  opportunities round-trip, time-series queries return within a latency budget.
- **Operations/chaos**: fault-injection fixtures drop the WSS connection, delay
  RPC, and inject a reorg; assert reconnect, failover, and correct
  invalidation/retraction of opportunities derived from orphaned blocks.

## 7. Latency tier

- `pytest-benchmark` measures the hot path (decode → graph update → detect →
  price → gate) and per-stage costs.
- Budgets/SLOs from `docs/LATENCY.md` are asserted; a regression beyond a
  tolerance **fails CI**. Baselines are stored (`--benchmark-autosave`) and
  compared.

## 8. Data-currency & integrity in tests

To honour *"verifiable current, correct, only real live blockchain data"*:
- `verify`-tier tests re-derive sampled pool state from a **pinned block** and
  assert agreement with the independent oracle (Blockscout MCP / second RPC). See
  `docs/DATA_INTEGRITY.md`.
  - **Capture the exact integers.** Blockscout MCP `read_contract` decodes large
    uints through JSON floats (lossy above 2⁵³ — it rounded a real reserve by
    ~18 000 wei and a `sqrtPriceX96` by ~2.5e11). Capture via raw `eth_call`
    (`direct_api_call` → `/json-rpc`) and decode the hex, as the fixtures do.
- A static test (`test_no_synthetic_data_in_runtime`) scans `src/l2arb` runtime
  modules to ensure no synthetic/fixture data module is importable from them —
  mocks stay in `tests/`.
- Freshness tests assert every `Quote`/`Opportunity` carries a `Blockstamp` and
  that stale state is rejected/flagged.

## 9. CI wiring

`.github/workflows/ci.yml` runs, on every push/PR:
`lint → types → pairing → unit → integration(+db, services via compose/testcontainers)
→ verify → benchmark(gate) → audit`. All must be green to merge. The fast subset
(`make check`) also runs locally in pre-commit so red never reaches CI.

## 10. Test authoring conventions

- One behaviour per test; name states the expectation
  (`test_out_given_in_matches_onchain_quoter_within_1_wei`).
- Arrange real-shaped data; avoid over-mocking — prefer a fork to a hand-mocked
  RPC when feasible so tests fail when reality changes.
- Fixtures in `tests/conftest.py`; pinned blocks and pool addresses in
  `tests/fixtures/` as data, documented with the chain + block they were captured
  at so anyone can re-verify them on a block explorer.
