# PROGRESS — the Ralph loop's memory

This file is the single source of truth for build state. Each iteration updates
it. Fresh contexts read it to know what's done and what's next. Keep it honest.

> **Status:** M0–M10 built **plus a post-M10 production-audit hardening pass** — all
> Tier-A deterministic gates pass on HEAD (build + `clippy -D warnings` +
> `fmt --check` + **146 tests + the gated live-engine e2e**; benches within the §8
> budget; live HTTP smoke green on all 5 chains). **M0–M6 merged as PR #2; M7–M9 as
> PR #3; M10 as PR #4**; the audit hardening ships on a fresh branch-from-`main`.
> **NOT RALPH-COMPLETE**, but the **M8 real-engine gate is now CLOSED**: in the combined
> workspace the co-located `l2arb` engine drives our real `EngineClient` to a
> contract-validated `net_profit>0` (`tests/live_engine.rs` + `scripts/e2e_engine.sh`).
> Remaining for the sentinel: the full-pipeline live e2e (`l2-ingest` over real RPC) and
> a sustained live-WS soak (no WS endpoint here). Every gate is recorded honestly; none
> is faked green.

## Environment facts (verified 2026-07-15)
Recorded so future iterations don't re-discover them:
- **Toolchain:** Rust 1.94.1 (cargo/clippy/rustfmt), 4 cores, ~30 GB disk. Cargo
  fetches crates.io fine through the agent proxy.
- **Live L2 RPCs reachable (HTTP):** Arbitrum `arb1.arbitrum.io/rpc`, Base
  `mainnet.base.org`, Optimism `mainnet.optimism.io`, Unichain
  `mainnet.unichain.org`, Ink `rpc-gel.inkonchain.com`. Archive `eth_call` works
  ≥5000 blocks deep on Arbitrum → **real pinned-block fixtures are capturable**
  for M2–M7. (These public endpoints are HTTP-only / no WS and are rate-limited,
  so they seed *fixtures*, not the live hot path.)
- **`l2arb` engine is NOT installable** — not published to PyPI (`pip`/PyPI 404,
  2026-07-15). See Blocked items; affects M8's real-engine contract test and
  M10's live e2e/soak. Everything else is buildable and testable with real data.

## Next task
> **All Tier-A milestones (M0–M10) are complete on HEAD.** The only work left is the
> two Tier-B gates that cannot run in this environment; both are BLOCKED, not
> skipped. To close them and earn RALPH-COMPLETE, a future iteration with the
> prerequisites provided must:
> 1. **Full-pipeline live e2e** — the M8 real-engine gate is now CLOSED (engine
>    co-located; run `scripts/e2e_engine.sh`). What remains is the M10 e2e: run
>    `l2-ingest --config config.toml` against real RPC + the co-located engine so that
>    live-ingested pools produce `net_profit > 0` opportunities validated against
>    `docs/reference/INTEGRATION.md` end-to-end (needs a verified live pool registry).
> 2. **Provide a WebSocket L2 endpoint** (`L2I_WS_<chain_id>`), then run
>    `scripts/soak.sh` for the sustained soak: `newHeads`/`logs` live, reorg +
>    reconcile exercised, hot-path p99 within §8 budget under load (closes M1 WS + M10
>    soak). Only when BOTH pass on HEAD, append the RALPH-COMPLETE sentinel with an
>    evidence line. See `BUILD_PLAN.md → M10`.

## Milestone checklist
Tick `[x]` only when the milestone's **exit-criteria tests all pass** on HEAD.

- [x] **M0** Bootstrap & engine contract in code (workspace, CI, `core`, golden serde)
- [x] **M1** RPC & transport (alloy WS/HTTP, Multicall3, reconnect) — 5 chains
      _(Tier A green; Tier-B live: Multicall3 present + reads verified live on all
      5 chains; newHeads-over-WS BLOCKED — no WS endpoint available here.)_
- [x] **M2** On-chain validation gate & pool registry
- [x] **M3** AMM math & native-price derivation (pure)
- [x] **M4** V2 ingestor (Sync → mirror; event-derived == eth_call @ N)
- [x] **M5** V3 ingestor (Swap/Mint/Burn; == slot0/liquidity @ N)
- [x] **M6** Uniswap V4 adapter (Unichain; PoolManager by poolId; == StateView @ N)
- [x] **M7** Gas & price context (OP GasPriceOracle / Arbitrum ArbGasInfo)
- [x] **M8** Aggregator & engine client (sync snapshots; **real-l2arb contract test
      CLOSED** — the co-located engine drives our real client to a validated
      `net_profit>0`; `tests/live_engine.rs` + `scripts/e2e_engine.sh`. Documented-mock
      `contract_test.rs` stays as the always-on Tier-A conformance proof.)
- [x] **M9** Output sink, reorg, reconcile, observability
- [x] **M10** Hardening: `app` binary (config→validate→supervisor→wire), benches,
      `Dockerfile`, soak harness, graceful shutdown + `SIGHUP` reload _(Tier-A green:
      121 tests + benches record p50/p99 far under the §8 5 ms budget — decode_sync
      ~8.3 ns, v2_get_amount_out ~84 ns, mirror_apply ~38 ns, snapshot+build/64pools
      ~24.5 µs. **Tier-B live e2e + sustained soak BLOCKED** — need real `l2arb` +
      live WS; harness built + runnable, full green gate awaits the engine.)_

## Blocked items
- **RESOLVED (2026-07-24): M8 contract-with-real-`l2arb` test** — the combined
  workspace co-locates the `l2arb` engine as a sibling repo, so the real engine now
  drives our real `EngineClient` end-to-end. `tests/live_engine.rs` sends a known
  two-pool WETH/USDC dislocation through the subprocess (or HTTP) transport and asserts
  the real engine returns a contract-valid `net_profit>0` (`validate_response`,
  INTEGRATION.md §10). It is **gated** on `L2ARB_ENGINE_URL`/`L2ARB_ENGINE_CMD`, so the
  Tier-A gate stays deterministic when the engine is absent — not `#[ignore]`-dodged.
  Evidence: `./scripts/e2e_engine.sh` → "OK real engine: 2 opp(s); top
  net_profit=997439185756644654 (456.6 bps)". The documented-mock `contract_test.rs`
  remains the always-on Tier-A conformance proof (our client/request-builder), not the
  engine's math.
- **BLOCKED: M10 Tier-B live e2e + sustained soak** — the plug-and-play e2e and
  the soak both require the live `l2arb` engine (same reason as above) and a
  long-running WS endpoint. The pieces that *can* run here are built and green:
  criterion benches record p50/p99 far under the §8 5 ms budget, `scripts/soak.sh`
  is a runnable Tier-B harness, and `l2-ingest --check-config` loads+validates the
  example config end-to-end. The full Tier-B green gate (real opportunities with
  `net_profit>0` over a sustained live WS session) is BLOCKED on the engine + WS.
- **BLOCKED: M1 Tier-B `newHeads`/`logs` over WebSocket** — the public HTTP RPCs
  reachable here do not expose WebSocket, so the live `newHead` subscription
  smoke cannot run in this environment. The WS code path (`AlloyProvider::
  subscribe_heads`/`subscribe_logs`) is implemented and compiles; it is
  exercised by the live smoke only when an `L2I_WS_<chain_id>` endpoint is
  provided. The HTTP + Multicall3 portion of the M1 live smoke DID run here and
  passed on all five chains (block heights read, Multicall3 code present,
  `aggregate3` working).

## Iteration log
_(append one short entry per iteration: what was done, tests added, commit SHA)_

- **iter 1 — M0 done.** Workspace (`Cargo.toml`, `rust-toolchain.toml`, `.gitignore`,
  CI `ci.yml`) + `crates/core` (`l2i-core`): `DecU256`/`DecI256` decimal-string
  serde; `Token`, `Pool`/`PoolKind`/`PoolAddress`/`Blockstamp`/`V2State`/`V3State`,
  `ChainContext`/`DetectRequest`, `CrossChain`/`Asset`/`Bridge`/`Representation`,
  `DetectResponse`/`Opportunity`/`Leg`/`Risk`/`Block`/`EngineError` — matching
  `reference/INTEGRATION.md` field-for-field. Tests (12): big-int decimal-string
  round-trip at 2¹¹²/2¹²⁸/2¹⁶⁰/2²⁵⁶ + hex/number/empty rejection; golden
  serialize of the contract example (`tests/fixtures/*.pretty.json`, real
  WETH/USDC + real Arbitrum block 200000000 header); response deserialize;
  request/response JSON round-trips; V4 poolId identity. Tier A green
  (`fmt --check`, `clippy -D warnings`, `cargo test`).
- **iter 2 — M1 done (Tier A).** `crates/chains` (`l2i-chains`): `ChainSpec` +
  gas model + canonical predeploys (Multicall3, OP GasPriceOracle/L1Block, Arb
  ArbGasInfo) for the 5 chains, lookup by id/name. `crates/rpc` (`l2i-rpc`):
  `error`; `backoff` (exp ceiling + full jitter, SplitMix64); `multicall`
  (Multicall3 `aggregate3` sol! codec); `frame` (WS subscription envelope +
  `HeadSummary`); `reconnect` (mockable connect-with-retry + `Sleeper`);
  `coalesce` (single-flight); `provider` (`ChainProvider` trait + `AlloyProvider`
  HTTP/WS). Tests: chains 5; rpc lib 17 (backoff schedule, jitter bounds,
  multicall selector/roundtrip, reconnect success/retry/give-up, single-flight
  coalesce); multicall codec vs **recorded real Arbitrum aggregate3 response**
  (fixture); frame decode vs **real Base header + WETH log** (fixtures). Tier-B
  live smoke run here against public HTTP: all 5 chains OK (block height,
  Multicall3 present, aggregate3). Tier A green.
- **iter 3 — M2 done (Tier A).** `crates/registry` (`l2i-registry`): `schema`
  (V2/V3/V4 registry entries + TOML loader + `load_registry_file`); `abi`
  (token0/token1/fee/factory/decimals/symbol/getReserves/StateView sol! bindings,
  `symbol` bytes32 fallback, V4 `PoolKey` + `compute_pool_id`); `gate` (the §7
  validation gate: code-exists → token0/1 → fee(V3) → deny-list → decimals/symbol
  → factory; V4 hook-safety → poolId → metadata; structured `RejectReason`, loud
  `tracing::warn` reject/park, `GateOutcome`). Added `rpc::mock::MockProvider`
  (feature `testing`) replaying recorded reads. Tests (13): gate over **recorded
  real** Arbitrum reads at a pinned block — real Uniswap V3 WETH/USDC 500 pool +
  Camelot V2 pair validate, metadata matches the fork read; corruptions reject
  (wrong token0, wrong fee, non-contract, denied token, wrong factory); V4
  `compute_pool_id` **matches a real Unichain Initialize poolId**, unsafe hook
  rejected, safe-listed hook accepted, poolId-mismatch rejected; example registry
  loads. Fixtures captured on-chain (`arbitrum_gate.json`,
  `unichain_v4_initialize.json`). Tier A green.
- **iter 4 — M3 done (Tier A).** `crates/amm` (`l2i-amm`, pure): `v2`
  (`get_amount_out`/`get_amount_in`, saturating so full-range inputs never panic);
  `v3` (`get_sqrt_ratio_at_tick` — faithful Uniswap v3-core TickMath port —,
  `sqrt_price_x96_to_price`, q96); `native` (`native_price_in`, V2/V3
  `*_price_native_in_t`, `build_native_price_map` that omits no-path numeraires).
  Tests (19): V2 `get_amount_out` matches textbook 0.30% vectors AND a **real
  Camelot WETH/USDC reserve snapshot** exactly; V3 `get_sqrt_ratio_at_tick`
  matches canonical Uniswap constants (tick 0 → 2⁹⁶, MIN/MAX_SQRT_RATIO) and the
  **real slot0 tick↔sqrtPrice invariant**; V3 price matches the **real WETH/USDC
  slot0** (ETH≈$1919) within 1e-9 and `native_price_in[USDC]` matches; proptest —
  `get_amount_out` no-panic over full U256 + monotonic + below-reserve,
  `get_sqrt_ratio_at_tick` monotone over the whole tick range, price>0. Fixture
  `arbitrum_amm.json` captured on-chain. Tier A green.
- **iter 5 — M4 done (Tier A).** `crates/ingest` (`l2i-ingest`): `event`
  (`Sync(uint112,uint112)` sol! event + `decode_sync`/`decode_sync_reserves`,
  topic verified); `mirror` (`DashMap` `Mirror` + `PoolState`/`LiveState`,
  `to_core_pool`, `apply_v2_sync`, `snapshot`/`snapshot_verified`,
  `set_verified`); `v2` (`seed_v2_pools` — batched `getReserves` multicall seed,
  `decode_reserves`). Tests (6): **event-derived reserves == `getReserves` at
  block N, exactly** (real Arbitrum DIA/USD₮0 pool, last Sync in the block vs the
  eth_call); multicall seed reproduces the same reserves (real
  `aggregate3([getReserves])` response); emitted `Pool` round-trips through the
  contract JSON with `kind:"v2"`, decimal-string reserves, `verified:true`; Sync
  topic/length unit tests. Fixture `arbitrum_v2_sync.json` captured on-chain.
  Tier A green.
- **iter 6 — M5 done (Tier A).** `crates/ingest` V3: `event` adds V3
  `Swap`/`Mint`/`Burn` sol! events, `decode_v3_swap` (sqrtPriceX96/liquidity/tick
  from data, int24 sign-extension) and `decode_v3_liquidity_change` (tickLower/
  tickUpper from indexed topics + amount); `mirror` adds `apply_v3_swap` and
  `apply_v3_liquidity_change` (in-range bracket refresh); `v3` adds
  `seed_v3_pools` (slot0()+liquidity() multicall, 2 calls/pool). Tests (7 new):
  **event-derived sqrtPriceX96/tick/liquidity == slot0()/liquidity() at block N,
  exactly** (real Arbitrum ESP/USDC 0.01% pool, block's last Swap); multicall seed
  reproduces it (real `aggregate3([slot0,liquidity])`); emitted `Pool` round-trips
  with `kind:"v3"` + decimal-string sqrtPrice/liquidity + int tick; Mint/Burn
  refresh only when the range brackets the tick. Fixture `arbitrum_v3_swap.json`
  captured on-chain. Tier A green.
- **iter 7 — M6 done (Tier A).** `crates/v4` (`l2i-v4`): `event` (V4
  `Swap`/`ModifyLiquidity` sol! events; `decode_v4_swap` — sqrtPriceX96/liquidity/
  tick/**fee** by poolId — + `decode_v4_modify_liquidity` signed delta);
  `stateview` (`getSlot0`/`getLiquidity` calldata+decode, `seed_v4_pools` via
  StateView multicall, `effective_fee` dynamic-vs-static); `adapter` (`hook_is_safe`
  gate, `apply_v4_swap` with dynamic-fee update, `apply_v4_modify_liquidity`
  signed-delta refresh). Added `Mirror::set_fee_pips`. Tests (6): **V4 event-derived
  sqrtPriceX96/tick/liquidity == StateView.getSlot0/getLiquidity at block N,
  exactly** (real Unichain pool, block's last Swap); StateView multicall seed
  reproduces state (real aggregate3, emits kind:v3 with poolId identity); real
  dynamic-fee M2 pool → emit kind:v3 with poolId address + fee read from the Swap
  event; hook gate accepts 0x0/safe-listed, rejects unknown; effective_fee dynamic
  vs static. Fixture `unichain_v4_swap.json` captured on-chain (StateView
  `0x86e8631A…`). Tier A green.
- **iter 8 — M0–M6 merged (PR #2 → main); M7 done (Tier A).** New crate
  `crates/gas` (`l2i-gas`) — NOTE: the build plan puts gas adapters in `chains`,
  but `chains` is depended-on by `rpc`, so adapters that do RPC reads must live in
  a crate *above* `rpc` to avoid a `chains↔rpc` cycle; the predeploy address
  constants stay in `chains`. `l2i-gas`: `getL1Fee(bytes)` sol! codec;
  `read_gas_price` (added `ChainProvider::gas_price` + mock `with_gas_price`);
  `read_l1_data_fee` (OP-Stack `GasPriceOracle.getL1Fee`, Arbitrum → 0);
  `assemble_chain_context` (+ re-exported `build_native_price_map`). Tests (4):
  Base `gas_price_wei` and `getL1Fee(sample)` match the **real oracle reads at a
  pinned block**; Arbitrum `l1_data_fee_wei == 0` + gas price matches;
  `getl1fee_calldata` reproduces the recorded calldata; `ChainContext` omits a
  numeraire with no native-price path. Fixture `gas.json` captured on-chain. Tier A
  green.
- **iter 9 — M8 done (Tier A).** `crates/aggregator` (`l2i-aggregator`): `snapshot`
  (`re_stamp` — one block per chain per request —, `IncrementalTracker` +
  `state_fingerprint` excluding blockstamp); `request` (`build_detect_request`,
  `IncrementalPolicy` first-request-full); `cadence` (debounce floor + heartbeat
  ceiling, on_change/interval/hybrid); `crosschain` (`filter_cross_chain` keeps
  assets on ≥2 chains, prunes dangling bridges/pairs). `crates/engine-client`
  (`l2i-engine-client`): `EngineClient` trait; `HttpEngineClient` (keep-alive
  reqwest, POST /detect, GET /health, documented error shape); `SubprocessEngineClient`
  (`python -m l2arb.api.runner` stdin→stdout); `validate_response` (§10 checks).
  Tests (18): snapshot invariants (proptest — one block/chain, incremental only
  changed, first request full); cadence/policy/crosschain units; **engine-client
  conformance vs a wiremock server + subprocess** (health/detect, blockstamps
  round-trip, error shape, validate flags bad responses). Fixed workspace reqwest
  feature (`rustls-tls`→`rustls`+`webpki-roots` for 0.13). **BLOCKED:
  real-`l2arb` net_profit>0 contract test — engine unavailable; documented-mock
  conformance proven instead, never faked.** Tier A green.
- **iter 10 — M9 done (Tier A).** `crates/ingest` `reorg` (`ReorgTracker` — ring of
  recent heads, classifies Extended/Duplicate/Reorg{common_ancestor}/Gap) +
  `Mirror::mark_unverified_after`; `reconcile` (`reconcile_v2`/`reconcile_v3` —
  independent eth_call vs mirror). `crates/output` (`l2i-output`): versioned
  `Envelope{schema_version,kind,chain_blocks,payload}`, `OutputSink` trait,
  `StdoutSink`, `WsServerSink` (tokio-tungstenite broadcast); `sink_from_config`
  (ws/stdout built; redis/grpc loudly `Unavailable` — config surface exists, not
  built this milestone). `crates/observability` (`l2i-observability`): Prometheus
  `install_metrics`/`/metrics` + `/health` (axum), `LatencyTimer`, `init_tracing`.
  Tests (10 new): **reorg rollback → verified:false → no stale emission → recover**;
  **reconcile mismatch → verified:false, then match**; envelope schema; **WS
  subscriber receives snapshot+opportunities**; /health + /metrics render. Added
  workspace deps axum/tokio-tungstenite, bumped metrics-exporter 0.18. Tier A green.
- **iter 11 — M10 done (Tier A); build complete.** `crates/config` (`l2i-config`):
  typed `Config` mirroring `config.example.toml` (engine/cadence/output/observability/
  infra/chains/cross_chain), `parse`/`load`/`validate`/`enabled_chains`; user-fillable
  addresses stay `String` (gate validates reality), only stable infra addresses typed
  as `Address`. `crates/app` (`l2-ingest` binary): `main` (arg parse, `--config`/
  `--check-config`/`--help`, load→validate→`init_tracing`→run); `pipeline` (observability
  serve → `build_engine` http|subprocess → `sink_from_config` → per-chain
  `connect_and_seed` [connect → validation gate → `seed_all` → spawn `ChainIngestor`]
  → `aggregator_loop` [cadence tick → `snapshot_verified` → `re_stamp` → `build_context`
  (live gas reads) → `build_detect_request` → `engine.detect` → `validate_response` →
  publish `Envelope`] → SIGINT/SIGTERM shutdown + SIGHUP reload); `ingestor`
  (`ChainIngestor` live actor: `subscribe_heads`→reorg, `subscribe_logs`→decode→mirror,
  reconcile tick; `seed_all` V2+V3+V4). `crates/benches` (`l2i-benches`): criterion
  hot-path — decode_sync ~8.3 ns, v2_get_amount_out ~84 ns, mirror_apply_v2_sync
  ~38 ns, snapshot_and_build_request_64pools ~24.5 µs (all far under the §8 5 ms
  decode→emit budget). `Dockerfile` (multi-stage, non-root runtime, EXPOSE 9001/9090/
  9100); `scripts/soak.sh` (Tier-B soak harness, documents BLOCKED); README build/run
  quickstart updated. Tests (2 config + benches); manual `--check-config` against
  `config.example.toml` prints a clean summary, exit 0. **Tier-B live e2e + sustained
  soak BLOCKED** — need real `l2arb` + live WS (harness runnable, gate awaits engine).
  RALPH-COMPLETE deliberately NOT written: it requires those Tier-B gates green. Tier-A
  green (build + `clippy -D warnings` + `fmt --check` + 121 tests).
- **iter 12 — production-audit hardening (post-M10).** A four-lens audit (hot-path,
  math/decode, integration/resilience, persistence/config/observability) found real
  gaps behind the M10 "hooks"; each is now implemented + tested (145 tests, +24):
  1. **Reorg spurious-reorg bug FIXED** (`ingest/reorg.rs`): a provider re-delivering
     an identical old head was misclassified `Reorg` (wiping verified pools + cascading
     a bogus `Gap`). Now checks the stored hash at that height → `Duplicate`. +3 tests.
  2. **Live reconciliation WIRED** (`ingest/reconcile.rs::reconcile_pool` +
     `app/ingestor.rs`): the reconcile tick now samples verified pools (rotating), re-
     reads each at its blockstamp, flips `verified:false` + meters `RECONCILE_MISMATCHES`
     on drift. The "our data is real, forever" proof runs live. +1 test.
  3. **Reconnect/backoff SUPERVISOR** (`app/pipeline.rs`): each chain runs under a
     supervise loop — on any exit it marks the mirror `verified:false` (no stale
     emission), reconnects with `Backoff`, reseeds (bumps a generation), retries;
     boot-time connect failures now retry instead of abandoning the chain. Stream-end
     now returns `Err` instead of lingering (`ingestor.rs`).
  4. **Real cadence** (`app/pipeline.rs`): `Cadence::should_send` now gates sends
     (was dead `let _ = cadence`), driven by an O(1) `Mirror` version counter — on-
     change/heartbeat honored; incremental tracker/policy reset on reseed (fixes the
     first-request-must-be-full contract after reconnect).
  5. **Warm-start persistence** (`ingest/persist.rs`, new): atomic snapshot of the
     verified mirror + restore-on-boot as **`verified:false`** (honest — re-verified
     by the live/reconcile path), skipping the cold-seed RPC storm. `[cache]` config.
     +7 tests.
  6. **Cached per-chain `ChainContext`** (`app/context.rs`, new): gas + native prices
     move off the per-tick path onto a `watch` channel refreshed per block — removes
     2×C serial RPCs per tick. **native_price_in now derived** from live WETH/T pools
     (was empty `BTreeMap`). +2 tests (real WETH/USDC → 1.9198e-9).
  7. **Cross-chain WIRED** (`app/crosschain.rs`, new): config→`CrossChain` conversion
     + `filter_cross_chain` (was hardcoded `None`). +3 tests.
  8. **Config validation hardened** (`config/lib.rs`): schema_version, chain_id/gas_model
     vs registry, endpoint/engine-URL shape, sink+bind, cadence/timeout bounds — makes
     `--check-config` authoritative. +8 tests.
  9. **Metrics wired** (`observability` + call sites): `HOTPATH_SECONDS`,
     `ENGINE_DETECT_SECONDS`, `VERIFIED_POOLS`, `RECONCILE_MISMATCHES`, plus new
     `HEAD_GAPS`/`INGESTOR_RECONNECTS`/`CHAINS_LIVE` (were defined-but-never-emitted).
  10. **V4 fee-decode panic FIXED** (`v4/{event,stateview}.rs`): `U256::to::<u32>()`
     panicked on a malformed word → now reads the low 4 bytes (no crash).
  11. **Reorg depth safety-margin** (`app/ingestor.rs`): multi-block reorgs invalidate
     `common_ancestor − 2` (depth is unknowable at first sight; conservative = safe).
  The AMM math, all decoders, and the gas path were independently re-verified CORRECT.
  An adversarial self-review of the whole diff then drove 7 more fixes: context primed
  with real gas *before* seed (no `gas_price=0` phantom-profit window); context-refresh
  + reconcile moved to **off-loop workers** (RPCs never stall log draining); native-price
  now *verifies* the WETH side against a configurable `weth` (omits a mis-wired pool
  instead of mispricing it, +1 test); unique snapshot temp files (no shutdown-flush
  corruption); incremental tracker populated on full requests (no redundant resend);
  `set_verified` bumps only on real change; honest `Gap` comment. Live HTTP smoke green
  on all 5 chains. **Tier-B (live WS + real `l2arb`) still BLOCKED**; the async
  supervisor/reconnect/reconcile logic is unit-tested at the component level, full live
  exercise awaits those endpoints. RALPH-COMPLETE still NOT written. Tier-A green
  (build + `clippy -D warnings` + `fmt --check` + 146 tests).
- **iter 13 — combine step 1: Seam A (ingest → engine) un-blocked live.** The four
  L2-arb repos are being combined into one system (conductor pattern; L2_bots stays the
  read path). The first seam is L2_bots → `l2arb`, and with the engine co-located it is
  now proven live: added `crates/engine-client/tests/live_engine.rs` (gated on
  `L2ARB_ENGINE_URL`/`L2ARB_ENGINE_CMD`) + `scripts/e2e_engine.sh`. The **real** engine,
  driven through our real `EngineClient` over both the subprocess and HTTP transports,
  detects a known two-pool WETH/USDC arbitrage and returns a contract-valid
  `net_profit>0` (`validate_response`, §10). Closes the M8 "real-l2arb net_profit>0"
  gate that was BLOCKED only for lack of the engine — never faked green; the always-on
  `contract_test.rs` mock proof is untouched. Evidence: `OK real engine: 2 opp(s); top
  net_profit=997439185756644654 (456.6 bps)`. Remaining Tier-B: full-pipeline live e2e
  (`l2-ingest` over real RPC) + WS soak (no WS endpoint here). Tier-A green — the gated
  test skips deterministically with no engine and runs green with it.

<!--
  When the entire BUILD_PLAN is satisfied (Tier A green on HEAD AND Tier B
  live gates passed), append a line below beginning exactly with RALPH-COMPLETE
  plus a one-line evidence summary. The loop runner stops when it sees it.
  Do NOT add it prematurely.
-->
