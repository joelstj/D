# Research notes — cross-chain flash-loan arbitrage gap audit (2026-08-04)

Branch `claude/cross-chain-flash-loan-gaps-v2fki8`. Scratch/anchor doc per the TDD loop — if
something built later contradicts a finding here, stop and reconcile rather than pushing forward.

## Task

"Run a granular audit and find all of our gaps that are preventing us from executing successful
cross chain flash loan arbitrage trades."

## Framing (confirmed, not assumed, before auditing anything)

Cross-chain arbitrage in this repo is — correctly and honestly, by design — **not** an atomic
flash loan. `contracts/contracts/crosschain/CrossChainArbitrageExecutor.sol`'s own NatSpec and
`contracts/docs/specs/10-cross-chain.md` both state plainly that a transaction cannot span two
chains, so "atomic cross-chain flash-loan arbitrage" is not a real thing the EVM can do. The
model actually implemented is **inventory-based and non-atomic**: `executeSourceLeg` (chain A)
swaps held inventory and dispatches it across a bridge; later, `executeDestinationLeg` (chain B)
swaps the arrived funds into the target asset. Between the two, capital is in flight and exposed
to price movement and bridge risk. This framing is not itself a gap — it's the accurate premise
the rest of this audit is measured against. "Successful cross-chain execution" therefore means:
the two-leg inventory flow works end-to-end, is honestly priced/risked, and is reachable by some
operational path — not that it becomes secretly atomic.

## Method

Five parallel read-only audits (general-purpose agents), each briefed with exact file paths from
initial orientation reading, each required to verify every claim by reading current source (not
trusting `CLAUDE.md` history) and to grep to confirm absence before reporting something missing:

1. `contracts/` — bridge adapters, deploy/registry wiring, on-chain risk controls, test proof
2. `engine/` — cross-chain detection/profit-gate correctness vs. the non-atomic reality
3. `ingestion/` — multi-chain aggregation and WS-envelope wiring
4. `dashboard/` — opportunity type fidelity, execution wiring, Contracts panel
5. Repo-wide — the spec-mandated off-chain orchestrator and risk-control layer

## Findings

Severity is about "how badly this blocks a real, successful cross-chain trade," not general code
quality. FIX = addressed this session with a regression test. RECORD = confirmed real, but out of
scope for a safe same-session fix (see reasoning per item) — tracked here as backlog, not faked.

### Contracts (`contracts/contracts/` — Gen1, the real/CI tree; `contracts/src/`+`ralph/` is a
separate, never-bootstrapped Gen2 skeleton, out of scope, confirmed untouched by this session)

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| C1 | CRITICAL | No real `IBridgeAdapter` implementation anywhere — only `MockBridgeAdapter` (pulls tokens, emits an event, delivers nothing cross-chain). `executeSourceLeg` cannot move funds cross-chain in production today. | **RECORD** — a real bridge integration (Across/Stargate/CCIP/…) is a protocol-selection + security-review task, not a same-session code fix. |
| C2 | CRITICAL | `bridgeAdapter` is caller-supplied with **no allowlist** and gets `forceApprove`d for the entire held balance — a compromised `EXECUTOR_ROLE` hot key can pass a malicious contract and drain the full inventory. Structurally identical to the `DexType.GENERIC` router risk already fixed on `FlashLoanArbitrage.sol` (root `CLAUDE.md` §8 item 7, `allowedGenericRouters` + `GUARDIAN_ROLE`-gated setter). | **FIXED** — mirrored the existing pattern: `allowedBridgeAdapters` mapping + `setBridgeAdapterAllowed`, `GUARDIAN_ROLE`-gated, deny-by-default. |
| C3 | CRITICAL | No off-chain orchestrator exists anywhere in the repo, in any language. Confirmed by grep: `orchestrator` appears only in spec/backlog prose (`ralph/BACKLOG.md` P9-T4 is unchecked). Nothing watches spreads, checks inventory, sizes within caps, executes both legs, rebalances, or hedges. | **RECORD** — Phase 9 by design, spec explicitly flags exposure caps as "NEEDS HUMAN." Building this speculatively (esp. hedging strategy and cap values) risks shipping something that looks safe but encodes unreviewed risk parameters. |
| C4 | HIGH | No deadline/timeout parameter on either leg function and no automatic refund path. If `executeDestinationLeg` is never called, bridged funds sit as an ordinary balance on the destination contract, recoverable only via privileged, manual `rescueTokens`/`GUARDIAN_ROLE`. | **RECORD** — a source-chain contract structurally cannot know destination-chain/bridge state without an oracle or the (nonexistent) orchestrator; a bolted-on on-chain timer wouldn't fix the real problem and risks a false sense of safety. Correct fix belongs with the orchestrator (off-chain deadline tracking + human-triggered unwind). |
| C5 | HIGH | No on-chain exposure cap. `minBridgeAmount`/`minOut` bound worst-case *slippage*, not trade *size*. | **RECORD** — spec assigns this to the orchestrator explicitly ("orchestrator refuses opportunities that would exceed it... NEEDS HUMAN"). A per-chain-contract cumulative cap can't be tracked correctly on one chain alone (chain A's contract can't observe chain B's settlement), so a partial on-chain version would be misleading, not genuinely safer. |
| C6 | HIGH | No sibling-executor registry on-chain or off-chain — `dstRecipient` is a bare caller-supplied address per call with zero cross-check. An operator/compromised key typo or malicious redirect has no guardrail. | **FIXED** — added a `siblingExecutor` registry (`chainId => address`), `GUARDIAN_ROLE`-gated; when a sibling is registered for `dstChainId`, `executeSourceLeg` now requires `dstRecipient` to match it. Additive: chains with no registered sibling behave exactly as before (no regression on existing tests). |
| C7 | MEDIUM | No inventory accounting or drift tracking anywhere — the one config field that gestures at it (`strategies.example.json`'s `pauseOnInventoryDriftPct`) has zero consumers. | **RECORD** — same root cause as C3 (orchestrator-owned concern). |
| C8 | MEDIUM | The dual-fork cross-chain proof is genuine (two independently-forked live chains) but only ever run manually — CI never runs `test:fork:crosschain`/`test:fork:polygon`, and the Foundry mirror has never executed (`forge` unavailable in every sandboxed session to date, reconfirmed this session). | **RECORD** — CI wiring for a fork test needs a live RPC secret decision that's an operator/ops call (matching how the existing Arbitrum fork job is already secret-gated); noted precisely rather than silently reattempted. `forge` unavailability reconfirmed empirically this session (see Environment section below). |
| C9 | MEDIUM | The "bridge" step in every existing test is a manual mint/transfer standing in for delivery, never a real bridge call. | **Not a defect** — already disclosed everywhere it appears (contract NatSpec, README, DEPLOYMENT.md, the notes file). Direct consequence of C1; no independent action needed. |
| C10 | MEDIUM | Documentation covers compile/deploy/test but never an operational runbook for actually running a cross-chain trade (no bridge protocol chosen, no timing guidance, `INTEGRATION.md`/example bots don't mention the cross-chain executor at all). | **RECORD** — writing an honest runbook ahead of C1/C3 existing would either be vague or imply more operational readiness than exists; better to land after the orchestrator/bridge work. |

### Engine (`engine/src/l2arb/detect/cross_chain.py`)

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| E1 | CRITICAL | Profit gate prices both legs off the *same instant* pool snapshot and applies zero price-drift discount for the real settlement wait (600s+ in the shipped fixture) — 5x past the engine's own 120s freshness bar for same-chain trust, with no acknowledgment of the gap. | **FIXED** — added a configurable, settle-time-scaled price-drift haircut to the cross-chain profit gate (opt-in at the pure-compute layer, sensible operator default at the API boundary — same pattern as the existing freshness gate, root `CLAUDE.md` §8 item 1). |
| E2 | CRITICAL | Emitted `Opportunity`/`Leg`/`BridgeQuote` carry no field an executor needs (`bridgeAdapter` address, `dstRecipient`, per-leg router/dexType/calldata) — detection data only, not a constructible route. Worse than the same-chain case: *zero* of the cross-chain-specific executable fields exist (vs. the same-chain path's partial gap already documented in root `CLAUDE.md` §10). | **RECORD** — same category as the already-accepted same-chain limitation (root `CLAUDE.md` §10): building a real router/calldata mapper is a substantial feature, and inventing plausible-looking route fields without one would risk fabricated execution data (invariant 1). |
| E3 | CRITICAL | No inventory/wallet-balance awareness anywhere — a pure price spread with zero knowledge of whether the operator holds any capital on the sell-side chain to act on it. A $10 spread and a $10M spread with no capital behind it are reported identically. | **RECORD** — inventory tracking is the orchestrator's documented job (C3); the engine has no wallet/balance concept for *any* strategy today, and bolting one on only for cross-chain would be inconsistent with the engine's detector-only role (root `CLAUDE.md` §2 item 2: "the engine is a detector, not a trader"). |
| E4 | HIGH | Cross-chain risk downweight is a flat constant (`cross_chain_success_penalty`), blind to `settle_seconds` even though that field is already computed and carried on the opportunity. A 30s fast-bridge opp and a 60min canonical-bridge opp get an identical confidence haircut. | **FIXED** — risk penalty now scales with `settle_seconds`, plus a drift-risk note in `RiskAssessment.notes` per the engine's own design doc (`ARBITRAGE_THEORY.md` §5) promise. |
| E5 | HIGH | Numeraire cross-chain fungibility is never checked — only the bridged asset calls `registry.are_fungible()`; the numeraire only gets a strictly-weaker decimals-equality check, 20 lines from the check that should also apply to it. | **FIXED** — numeraire now also gated through `are_fungible()`, matching the asset check. |
| E6 | MEDIUM | Gas is correctly charged for both legs, but neither leg's estimate is increased for the bridge-adapter call itself — only the swap's gas is modeled. | **RECORD** — the "correct" bridge-call gas overhead is protocol-dependent and unknowable with confidence until a real adapter (C1) is chosen; a made-up constant now risks its own kind of fabricated-precision, lower priority than E1/E4/E5. |
| E7 | MEDIUM | Test suite covers bridge-fee bookkeeping and the decimals-phantom-profit regression well, but had zero coverage for time-in-flight drift, settle-time risk-sensitivity, or inventory/executability (because none of that existed yet). | **FIXED as part of E1/E4/E5** — new regression tests added for each; inventory awareness (E3) is intentionally out of scope so has no test (nothing to test). |

### Ingestion (`ingestion/crates/`)

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| I1 | HIGH | Cross-chain silently resolves to fully inert with zero operator-facing signal. `config.example.toml` ships `[cross_chain] enabled = true` with placeholder addresses; `--check-config` prints the raw unfiltered config (looks fully wired) without ever calling the real parse/filter path; the live process silently sends `cross_chain: None` forever, with no log line, metric, or `/health` distinction from "intentionally off." Same *shape* as prior CRITICAL/HIGH findings in this repo's own history (§9/§11: a silent, healthy-looking total outage). | **FIXED** — `build_cross_chain`/`filter_cross_chain` now emit a `tracing::warn!` when parsing/filtering drops the block to empty despite being configured-enabled, and `--check-config`'s summary now reports the real post-filter usable counts, not the raw config counts. |
| I2 | MEDIUM | `verified_pools` hashset in `validate_response` is keyed on bare `PoolAddress` (no `chain_id`), while the adjacent `sent_stamps` check two lines below correctly keys on `(chain_id, number, hash)`. Since the aggregator now always sends one combined multi-chain request, a verified pool on chain A can rubber-stamp a same-address pool reference on chain B — a real risk given several configured chains are OP-Stack siblings sharing identical predeploy addresses. | **FIXED** — `verified_pools` now keyed on `(chain_id, PoolAddress)`, matching the pattern already used two lines away. |
| I3 | MEDIUM | `filter_cross_chain` documents ("a real bridge between them... do not invent bridge routes") but doesn't enforce that an asset kept for having ≥2 chain representations also has an actual matching entry in `bridges[]` — the two filters run independently. Untested gap (`2 representations, 0 bridges` case has no test). | **FIXED** — asset-keep decision now also requires a matching bridge entry; added the previously-missing test case. |
| I4 | LOW | Cross-chain asset representations aren't cross-referenced against which chains are actually enabled in `[[chains]]`. Likely harmless (no pool data would exist for a disabled chain anyway) but unvalidated. | **FIXED** (bundled with I3, same file/function, cheap) — representations for a chain absent from the enabled chain set are now dropped with a debug log, not silently carried through. |

### Dashboard (`dashboard/backend/src`, `dashboard/frontend/src`)

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| D1 | CRITICAL | `ArbitrageOpportunity`/`RouteLeg` have exactly one `network`/`chainId` field; `engineMap.ts` resolves `chainId` from `o.numeraire.chain_id`, which — verified against the real producer — is *always* the source/buy chain. The destination chain, `is_cross_chain`, and `settle_seconds` are read off the raw engine payload but never written anywhere onto the mapped object. Systematic, not occasional. | **FIXED** — added `destChainId`/`isCrossChain`/`settleSeconds` to `ArbitrageOpportunity`, populated correctly in `engineMap.ts` from `chain_ids`/`is_cross_chain`/`settle_seconds`. |
| D2 | CRITICAL | Neither executor understands the two-transaction model. `PaperExecutor` models *every* revert as "atomic, lose only gas" — false for this contract, whose own NatSpec says capital is in-flight and exposed between legs. `LiveExecutor` refuses everything uniformly (safe, but not cross-chain-*aware*). | **FIXED** — `PaperExecutor` now detects `isCrossChain` opportunities (via D1's new field) and refuses to model them with atomic fill/revert semantics; they're marked `skipped` with an honest reason instead of fabricating a bounded-loss guarantee. `LiveExecutor`'s existing unconditional refusal already fails safe and needed no change. |
| D3 | HIGH | `qualifies()`'s network allowlist and `riskLimitBlock()`'s per-network cooldown both key off the single collapsed chain field — an operator who explicitly disables a chain has no way to keep a cross-chain opportunity targeting that chain as a *destination* from qualifying, because the destination chain was never structurally present to check. | **FIXED** — `qualifies()` now also checks `destChainId` (when present) against the operator's enabled `networks`, using D1's new field. |
| D4 | HIGH | The Contracts panel's deploy flow is hardcoded to `FlashLoanArbitrage`. Backend scaffolding for `CrossChainArbitrageExecutor` already exists (`CONTRACTS` list, `.env` prefix, readiness plumbing) but `deployParams()` takes no contract parameter and the frontend's deploy call hardcodes the artifact name — there is no way to deploy or record the cross-chain contract through the sanctioned MetaMask-signed flow. | **FIXED** — `deployParams(network, admin, contract?)` now accepts the target contract (defaults to `FlashLoanArbitrage` for backward compatibility), frontend gained a second "Deploy CrossChainArbitrageExecutor" action, and the readiness panel now renders `crossChainHasCode`. |
| D5 | MEDIUM | No "inventory balance per chain" concept anywhere in the dashboard — no settings field, no panel. `loanAmountUsd` is a flash-loan sizing concept that doesn't apply to this contract at all. | **RECORD** — same root cause as C7/E3 (orchestrator/business-config layer); a UI for a number the backend has no way to track yet would be decorative. |

### Repo-wide orchestrator/risk-control sweep

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| O1 | HIGH | Dashboard settings schema (`SettingsSchema`) has no exposure-cap, per-chain-inventory, or hedging field at all — confirmed by full-field read, not a grep miss. | **RECORD** — same root cause as C3/C5. |
| O2 | HIGH | No kill-switch tooling anywhere (dashboard/launcher/scripts) pauses *either* contract — zero `GUARDIAN_ROLE`/`pause(` usage outside the contracts themselves and their own tests. An operator must call `pause()` on each contract manually and separately. | **RECORD** — a MetaMask-signed "Pause/Unpause" panel mirroring the existing deploy-signing pattern is the right shape (safe: human-signed, fail-safe not fail-open) and is a reasonable near-term follow-up, but is a net-new UI feature, not a bug fix, and this session's dashboard batch was already substantial (D1-D4). Recorded with the concrete recommended design so a future session doesn't have to re-derive it. |
| O3 | — | `launcher/` reconfirmed as pure process-supervision infra (start/health/restart) — zero trading, inventory, or cross-chain awareness, by design (root `CLAUDE.md` §3: "infra recovery only, never touching the human-gated execution path"). | **Not a defect** — correctly scoped as-is. |
| O4 | CRITICAL (corroborating) | `executeSourceLeg`/`executeDestinationLeg` have zero callers anywhere in the repo outside the contract's own test files — confirmed by grep. The on-chain primitive is real and individually tested but currently unreachable from any other component. | Root-caused by C3 (no orchestrator) + E2 (no executable route data) + D2 (no execution wiring) above; no separate action, direct consequence of those three. |

## What already works correctly (rolled up, so this audit isn't one-sided)

- The non-atomic, two-transaction, inventory-based framing is honest and consistent everywhere it
  appears (contract NatSpec, all docs, dashboard's own prior "honest limitation" framing) — nothing
  in this codebase claims a fake atomic cross-chain flash loan.
- Both contract legs' swap mechanics reuse the already-hardened, already-audited `DexRouter`
  library with correct balance-delta accounting and route-seed validation.
- Deploy tooling (Hardhat + Foundry scripts) already deploys `CrossChainArbitrageExecutor` by
  default on both trees.
- Access-control baseline (`EXECUTOR_ROLE`/`GUARDIAN_ROLE` separation, `Pausable`,
  `ReentrancyGuard`) is solid and was not weakened by this session's changes.
- The one real dual-fork proof (live Polygon + live Arbitrum state in one coherent test run,
  simulated bridge delivery clearly disclosed) is genuine, not fabricated.
- Ingestion already does the structurally hard part right: one combined multi-chain `/detect`
  request per tick (required for cross-chain detection to be possible at all), and cross-chain
  opportunities pass through the same validation/WS path as same-chain ones with no silent same-
  chain-shaped drop.
- The engine's verified/freshness gate runs before pricing for both legs, uniformly with the
  same-chain path, and the decimals-mismatch phantom-profit guard (root `CLAUDE.md` §9 item 3) is
  real, correctly scoped, and remains regression-tested.
- The dashboard's risk limits (`maxConcurrentTrades`, `maxDailyLossUsd`) are global counters
  unaffected by the chain-collapsing bug, and `LiveExecutor` fails safe (refuses everything)
  rather than dangerously mishandling a cross-chain trade.
- The project's own documentation is unusually candid about its gaps — `contracts/README.md`
  states outright that hedging, exposure caps, and bridge-failure handling are not implemented,
  which meant this audit largely confirmed and scoped known gaps rather than uncovering hidden
  overclaiming.

## Environment (reconfirmed this session, matches prior sessions' recorded blockers)

- `forge`/Foundry: **not installed**, still BLOCKED. `cargo` 1.94.1, `node` 22.22.2, `pnpm`
  10.33.0, `python3` 3.11.15, `uv` 0.8.17 all available. Outbound Arbitrum RPC reachable
  (`arb1.arbitrum.io/rpc` → HTTP 200).

## Net result

13 confirmed defects fixed this session (C2, C6, E1, E4, E5, I1, I2, I3, I4, D1, D2, D3, D4),
each with a regression test, across contracts/engine/ingestion/dashboard. 15 further
findings recorded with precise reasoning for why a same-session fix would be premature or unsafe
(mostly: they are the off-chain orchestrator's job, and that orchestrator is Phase 9 by design,
gated on human-set risk parameters — building it speculatively this session would mean guessing at
exposure caps and hedging strategy, which is exactly the kind of thing root `CLAUDE.md` §2 says to
stop and record rather than fake). See per-finding disposition above for the reasoning behind each.
