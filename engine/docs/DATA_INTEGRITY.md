# Data Integrity — "only real, live, on-chain-verifiable data"

> This is a hard product requirement, not a nice-to-have. The engine's outputs
> are only meaningful if every number can be tied back to a specific block and
> independently confirmed. This document defines how that is guaranteed and
> tested.

## 1. The rule, precisely

A value may enter a **runtime** path only if it is one of:
1. **Read directly from chain** via `eth_call` / `eth_getLogs` / `eth_subscribe`
   against a configured RPC endpoint, **or**
2. **Deterministically derived** from (1) by a pure function in `amm/`,
   `graph/`, or `detect/` that has a unit test proving the derivation.

Everything carries a **`Blockstamp` = {chain_id, block_number, block_hash,
block_timestamp}** identifying exactly the state it came from. A value without a
blockstamp cannot be emitted.

**Forbidden in runtime paths:** synthetic/random data, hard-coded prices,
"example" numbers, cached values past their freshness window, and any third-party
*aggregated* price used as if it were ground truth. Aggregators (incl. CCXT CEX
prices) are allowed only as **offline sanity references**, clearly labelled, never
as the basis of an emitted opportunity.

## 2. Two-source verification (the bar for "verifiable")

Reading state from one RPC is necessary but not sufficient — an endpoint can lag,
lie, or be misconfigured. The bar is **agreement between two independent
sources**:

- **Source A (primary):** the engine's own RPC read (fast, streaming).
- **Source B (oracle):** an *independent* re-read at the **same pinned block**:
  - **Blockscout MCP** `read_contract` (e.g. `getReserves`, `slot0`,
    `liquidity`) — an independent explorer infrastructure, plus
    `get_contract_abi` / `inspect_contract_code` to confirm the contract is the
    DEX contract it claims to be, and `get_address_info` to confirm token
    metadata (decimals!).
  - or a **second, unaffiliated RPC provider**.
- A pool's state is **verified** when A and B agree at the same block within
  tolerance (0 wei for `getReserves`; ≤1 wei for quoter math). Disagreement flags
  the pool `UNVERIFIED` and excludes it from emission until resolved.

> **Capture the exact integers.** Blockscout MCP `read_contract` returns large
> uints as JSON floats — lossy above 2⁵³ (observed: a real V2 reserve rounded by
> ~18 000 wei, a `sqrtPriceX96` by ~2.5e11, router outputs by 1–590 wei). When the
> oracle read must be wei-exact, use a raw `eth_call` (`direct_api_call` →
> `/json-rpc`) and decode the hex, not `read_contract`. The captured verify
> fixtures are built this way.

The `verify/` subsystem (Phase 8) runs this continuously on a sampled rotation of
active pools, and the `verify` test tier asserts it in CI against pinned blocks.

## 3. Contract & token authenticity

Before a pool is trusted:
- Confirm the **factory** that created it is the known factory for that DEX
  (discovery via factory events, cross-checked with the oracle).
- Confirm token `decimals`/`symbol` on-chain (via `read_contract` /
  `get_address_info`) — never assume 18.
- Detect **proxy** contracts and resolve implementations (the oracle reports
  proxy info) so ABI decoding targets the right code.
- Record the verified `(chain, address, abi_hash, factory)` tuple; a change
  invalidates trust and re-triggers verification.

## 4. Freshness & currency ("verifiable current")

- Every subscription tracks head; every quote records the block it used.
- A configurable **staleness bound** (in blocks and in seconds) rejects or flags
  state older than the bound. L2 block times are short (~250ms–2s); the bound is
  set per chain.
- Opportunities include `age_blocks` / `age_ms`; a consumer can reject anything
  not derived from head or head-1.

## 5. Reorg safety

- The `reorg` tracker compares incoming `parentHash` to the known head; on a
  mismatch it walks back to the common ancestor.
- All state and opportunities derived from orphaned blocks are **invalidated and
  retracted** (emitted as a retraction event, and marked in the store).
- `chain`/operations tests inject a reorg on a fork and assert correct
  invalidation.

## 6. Reproducibility

Anyone must be able to reproduce and independently verify any reported
opportunity:
- The provenance record pins `{chain, block_number, block_hash, pool addresses,
  reserves used, size, math version}`.
- A `verify`-tier test (and a CLI, later) can replay: re-read those pools at that
  block via the oracle and recompute the opportunity, asserting it matches. This
  is the operational meaning of "verifiable on the blockchain."
- **Realized** (`tests/verify/test_onchain_amm.py`): real Base V2 + V3 WETH/USDC
  pools captured at a pinned block, replayed through the ingestion boundary, and
  asserted **bit-for-bit** against their on-chain router/quoter output.

## 7. How this shows up in code & tests

- `model.Blockstamp` is a required field on `Quote`, `PoolState`, `Opportunity`.
- `ports.VerificationOracle` abstracts the independent source; `oracle/blockscout.py`
  and `oracle/crosscheck.py` implement it.
- `test_no_synthetic_data_in_runtime` (static import scan) prevents fixture/mock
  data from leaking into `src/l2arb` runtime modules.
- `verify`-tier tests pin blocks and assert A≡B; fixtures document
  chain+block+address so a human can confirm on a block explorer.
- Any pool that cannot be two-source-verified is excluded from emission by
  construction (the engine filters on `verified is True`).

## 8. Note on the requested CEX/quant data sources

The brief lists many market-data sources (yfinance, Polygon, CCXT, etc.). Under
this policy they are confined to **offline research/backtesting and sanity
cross-checks** (`cex/`, `backtest/`), never to the live detection path, because
they are not on-chain-verifiable. CCXT CEX prices may power an *optional*
CEX↔DEX spread report, but that report is clearly separated and labelled as
using off-chain reference data.
