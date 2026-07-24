# Spec 07 — Gas optimization & Yul

Two goals: **lowest total fee** (dominated by L1 data cost on L2) and **smallest, leanest bytecode**
without sacrificing the security invariants. Speed and cost are the same objective here — a tighter hot
path is both cheaper and faster to land.

## Where the cost actually is
On OP Stack the **L1 data fee scales with (compressed) calldata size**; on Arbitrum there is an L1
poster surcharge on calldata (`docs/specs/01`). So the single biggest lever is **calldata size**, which
is why routes are compact byte blobs (`docs/specs/09`) decoded in Yul, not ABI-encoded structs. Measure
the L1 component, not just L2 gas, when optimizing.

## Yul / inline-assembly policy
Use assembly on the **hot path** where it measurably wins; keep everything else in readable Solidity.
Sanctioned uses:
- **Route decoding** — walk the calldata route with `calldataload`/offsets; no memory copies.
- **Low-level swaps/borrows** — hand-built `call`/`staticcall` with tight calldata construction to
  adapters/pools, avoiding ABI-encoder overhead on repeated shapes.
- **ERC20 transfer/approve** — minimal `SafeTransfer` handling non-standard (missing-return) tokens.
- **Balance-delta measurement** — cheap `balanceOf` staticcalls around a hop for fee-on-transfer safety.

**Every assembly block requires:** (1) a comment describing the memory region / free-memory-pointer and
stack it touches, and (2) a **differential test** proving equivalence to a plain-Solidity reference over
fuzzed inputs (**P7-T4**). Un-tested cleverness is rejected in review (`ralph/prompts/review.md`).

## Standard Solidity-level optimizations (baseline, not optional)
- **Custom errors** everywhere (`error Unprofitable(uint256,uint256)`) — no revert strings.
- **Packed storage**; group hot fields into one slot; cache `SLOAD` into memory; write once.
- **`immutable`/`constant`** for wiring set at deploy (registry pointers, owner init).
- **`calldata`** over `memory` for external inputs; never copy the route to memory to parse it.
- **`unchecked`** only with a one-line proof comment that overflow is impossible in range.
- **Minimize external calls**; batch reads; prefer `staticcall` for quotes. Avoid redundant approvals.
- **Short-circuit early**: deadline/access/pause checks before any state or external interaction.

## Transient storage (EIP-1153)
Use `TSTORE`/`TLOAD` for the **reentrancy guard** and **Uniswap V4 flash accounting** — they cost far
less than storage and auto-clear at tx end. **Gated by chain support** (`config` `"cancun"` flag,
`docs/specs/01`): on non-Cancun targets the guard falls back to a storage flag (build with
`FOUNDRY_PROFILE=shanghai`). The guard interface is identical; only the backing store changes.

## Contract-size discipline (EIP-170, 24KB)
Keep the executor small by pushing venue logic into **separate adapter contracts** (called via the
interface), not into the core. This also shrinks per-deploy cost and keeps the audited core minimal.
Watch `forge build --sizes`; a growing core is a smell.

## Gas budgeting & regression control
- Commit a `.gas-snapshot`; `scripts/verify.sh` can refresh it (`VERIFY_GAS=1`).
- Review mode diffs the snapshot and flags any hot-path growth (`ralph/prompts/review.md`).
- Track a per-strategy gas target (e.g. 2-hop under a documented budget) and record deltas in
  `ralph/PROGRESS.md` when the Yul pass lands (**P7-T4**).

## `via_ir` and the optimizer
Build uses `via_ir = true` with a high `optimizer_runs` (runtime-favoring) because the hot path runs
constantly and we care about runtime gas over deploy size (`foundry.toml`). The `fast` profile disables
IR for quick inner-loop iteration only — never for shipped artifacts.

## The rule that keeps it honest
Optimize **against a benchmark**, never on vibes. If a change doesn't move the gas snapshot or the L1
byte count, and it costs readability or a differential test, it doesn't ship.
