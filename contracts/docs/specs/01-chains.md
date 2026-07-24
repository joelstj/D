# Spec 01 — Chains, gas models & opcode support

Five L2s, two stacks. The differences that matter for an arbitrage engine are the **gas/fee model**,
the **opcode support level** (does transient storage exist?), and the **ordering/sequencer** model.
Everything address-specific lives in `config/chains/*.json` and must be verified (task **P1-\***).

> Chain IDs are stable and safe to rely on. **Every contract address in this doc is illustrative** —
> the canonical, verified values live in the config registries with `_source` citations.

## The five chains

| Chain | Chain ID | Stack | Native gas token | Notes |
|-------|---------:|-------|------------------|-------|
| Optimism (OP Mainnet) | 10 | OP Stack (Bedrock) | ETH | Mature DEX/lending set |
| Base | 8453 | OP Stack (Bedrock) | ETH | Deep liquidity; Aerodrome dominant |
| Ink | 57073 | OP Stack | ETH | Newer chain — provider/DEX set must be verified |
| Unichain | 130 | OP Stack | ETH | Uniswap-first; V4 primary venue |
| Arbitrum One | 42161 | Arbitrum Nitro | ETH | Different gas model + Timeboost ordering |

## Gas & fee models (this drives sizing and route compactness)

### OP Stack (Optimism, Base, Ink, Unichain)
Total fee = **L2 execution fee** (`gasUsed × L2 gas price`, cheap) **+ L1 data fee** (the dominant term).
Since the Ecotone upgrade the L1 data fee is charged on the **compressed transaction bytes** against L1
**blob** base fee, read via the `GasPriceOracle` predeploy (illustratively
`0x420000000000000000000000000000000000000F`; `L1Block` at `0x4200000000000000000000000000000000000015`).

**Consequence:** calldata size is a first-order cost. This is *why* routes are compact byte blobs
decoded in Yul (`docs/specs/09-route-codec.md`) rather than ABI-encoded structs — fewer L1 bytes = lower
fee. The off-chain gas-breakeven estimate must query the oracle for the current L1 blob base fee, not
just the L2 gas price.

### Arbitrum Nitro (Arbitrum One)
Uses ArbGas with a separate **L1 calldata surcharge** (the "poster" fee) and a distinct pricing curve.
Read L1 base fee / calldata pricing via the `ArbGasInfo` precompile (illustratively
`0x000000000000000000000000000000000000006C`); `ArbSys` at `...0064` exposes L2 block/tx context.
`tx.gasprice` semantics and the priority-fee auction differ from OP Stack — see `docs/specs/06`.

**Consequence:** the breakeven and tip modules are **per-stack**. Do not port OP-Stack fee math to
Arbitrum unchanged. Model each with its own oracle read.

## EVM / opcode support (gate transient storage!)

Transient storage (EIP-1153 `TSTORE`/`TLOAD`) and `MCOPY` arrived with the Cancun opcode set. Support
is **not uniform** across these chains/ArbOS versions and moves over time.

- Default build targets `cancun` (`foundry.toml`), using transient storage for the reentrancy guard and
  V4 flash accounting.
- A `shanghai` profile (no transient storage) is maintained as a fallback: `FOUNDRY_PROFILE=shanghai`.
- **Task P1-T1** must confirm, per chain, whether Cancun opcodes are active in the *shipped* environment
  and record it in `config/chains/*.json` as `"cancun": true|false`. The reentrancy guard and any
  transient-storage adapter select their implementation from that flag.

Rule of thumb (verify, don't trust): OP-Stack chains that have taken the Ecotone/Fjord/Granite line of
upgrades support Cancun; Arbitrum One supports transient storage from a sufficiently recent ArbOS. When
in doubt, ship the `shanghai` build to that chain.

## Ordering / sequencer model (drives MEV strategy)

- **OP Stack**: a single sequencer with (today) a private mempool and first-come ordering. Classic
  public-mempool frontrunning is largely absent, but the sequencer position and cross-domain effects
  remain. Strategy: submit sequencer-direct / via the chain's endpoint; rely on atomic revert; tune
  priority fee to influence ordering where applicable. (Fault-proof / decentralized-sequencer roadmaps
  will change this — keep the submission adapter pluggable.)
- **Arbitrum One**: fast sequencer plus **Timeboost**, an express-lane auction that lets a bidder get a
  short latency advantage. Relevant both as a way to win inclusion and as an MEV surface to model. See
  `docs/specs/06-mev-and-ordering.md`.

## Finality & reorg posture
L2 soft-confirmations are fast but not L1-final. For **same-chain atomic** arbitrage this is a
non-issue (the trade reverts or nets profit within one L2 block). For **cross-chain** (Phase 9) it is
central: never treat a source-chain fill as settled until it meets the settlement policy in
`docs/specs/10-cross-chain.md`.

## Config registry contract
Each `config/chains/<chain>.json` carries: `chainId`, `stack`, `cancun`, `gasModel`, predeploys/
precompiles, `flashProviders` (availability + addresses), `dexes` (family + addresses), and `tokens`
(address + decimals). Schema: `config/chains/schema.json`. Any unverified value is flagged
`"_verify": true` or left as the zero address — treat those as unknown until Phase 1 confirms them.

## Phase-1 checklist (mirrored in `ralph/BACKLOG.md`)
- [ ] Confirm chainId, block time, and Cancun status per chain.
- [ ] Confirm OP-Stack predeploys (WETH, GasPriceOracle, L1Block) per OP chain.
- [ ] Confirm Arbitrum precompiles (ArbGasInfo, ArbSys) and L1 fee accounting.
- [ ] Fill the flash-provider availability matrix (`docs/specs/02`).
- [ ] Fill the DEX registry (`docs/specs/03`).
- [ ] Confirm token addresses + decimals.
