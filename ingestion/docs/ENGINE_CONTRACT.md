# Engine contract → on-chain mapping (adapter spec)

This document is the bridge between **what the chains give us** (events, storage
slots, precompiles) and **what the engine wants** (the `DetectRequest` JSON in
[`reference/INTEGRATION.md`](reference/INTEGRATION.md)). It is normative: the
ingestion component MUST produce exactly these shapes with exactly these
semantics. When in doubt, the verbatim contract in `reference/INTEGRATION.md`
wins.

The golden rule that makes this whole component trustworthy:

> **We only ever emit pool state that the engine's math can price correctly, and
> every value we emit is reproducible from the canonical chain at the block we
> stamped it with.** If we cannot price a pool with the engine's V2 (constant
> product) or V3 (concentrated liquidity) math, we do not emit it. If we cannot
> verify a value on-chain, we mark `verified:false` and never let it drive a
> reported opportunity.

---

## 1. What the engine models — and what we must therefore exclude

The engine implements exactly two AMM math families:

| `kind` | Math | Emitted fields |
|--------|------|----------------|
| `v2`   | Constant product `x·y=k` (Uniswap V2 family) | `v2.reserve0`, `v2.reserve1` |
| `v3`   | Concentrated liquidity (Uniswap V3 tick math) | `v3.sqrt_price_x96`, `v3.tick`, `v3.liquidity` |

Therefore the following are **out of scope and MUST NOT be ingested** until the
engine gains support for them — feeding them in would produce mispriced,
phantom opportunities, which is the single worst failure mode for this system:

- **Solidly / Velodrome / Aerodrome _stable_ pools** — invariant is
  `x³y + y³x = k`, not `x·y=k`. (Their _volatile_ pools **are** `x·y=k` → ingest
  as `v2`. Their _Slipstream / concentrated_ pools are V3-style → ingest as `v3`.)
- **Curve / stableswap** pools — different invariant.
- **Balancer weighted / composable** pools — different invariant.
- **Uniswap V4 pools with non-trivial hooks** that change swap accounting
  (custom curves, dynamic fees we cannot read deterministically, JIT/withdrawal
  hooks). Only V4 pools whose hook is on the verified **safe-hook allow-list**
  (or the zero-address hook) may be ingested — see §4.
- **Fee-on-transfer / rebasing tokens** — reserves do not evolve as `x·y=k`
  predicts. Maintain a per-chain **token deny-list**; drop any pool that
  references a denied token.

Every excluded pool that a user _configured_ must be logged loudly at startup with
the reason, never silently dropped. A silent drop reads as "covered" when it is
not.

---

## 2. The pool object, field by field

```jsonc
{
  "address":  "0x…",          // pool identity (V4: the 32-byte poolId as 0x-hex, see §4)
  "kind":     "v2" | "v3",
  "fee_pips": 3000,            // fee in MILLIONTHS: 0.30%→3000, 0.05%→500, 1.00%→10000
  "verified": true,           // see §6 — false forbids the pool from driving a reported opp
  "token0":   { "chain_id", "address", "decimals", "symbol" },
  "token1":   { "chain_id", "address", "decimals", "symbol" },
  "blockstamp": { "chain_id", "number", "block_hash", "timestamp" },
  "v2": { "reserve0": "<dec-string>", "reserve1": "<dec-string>" },      // when kind=v2
  "v3": { "sqrt_price_x96": "<dec-string>", "tick": <int>, "liquidity": "<dec-string>" } // when kind=v3
}
```

Hard rules:

1. **Big integers are decimal strings, always.** `reserve*` reach 2¹¹²−1,
   `sqrt_price_x96` reaches ~2¹⁶⁰, `liquidity` reaches 2¹²⁸−1. Never serialize
   these as JSON numbers (silent precision loss). In Rust use `alloy_primitives::U256`
   → `.to_string()`. Add a round-trip test with a 2¹⁶⁰-ish value.
2. **`token0` / `token1` ordering is canonical**: `token0.address <
   token1.address` byte-wise (this is how the pool itself stores them). `reserve0`
   pairs with `token0`. Do not re-sort.
3. **`decimals` and `symbol` come from the ERC-20 contract**, read once at
   startup and cached, never guessed. Validate `0 ≤ decimals ≤ 36`.
4. **`fee_pips` is the pool's actual current fee.** For static-fee pools read it
   from the factory/pool (`fee()` on V3). For V4 dynamic-fee pools, read the
   live fee (see §4); if it cannot be read deterministically for the stamped
   block, the pool is not eligible (`verified:false`).
5. **`blockstamp` describes the exact block the state is true at** — see §5.

---

## 3. V2 and V3 ingestion (event-driven, zero extra round-trip)

The latency win of this component is: **read the new state out of the log the
chain already pushed us, instead of doing an RPC round-trip per block.**

**V2 (`UniswapV2Pair` and volatile Solidly pools):**
- Subscribe to the pair's `Sync(uint112 reserve0, uint112 reserve1)` event.
- `Sync` carries the *post-trade reserves directly* → write straight into the
  mirror. No `getReserves()` call needed on the hot path.
- Seed the mirror at startup with a batched `getReserves()` multicall, then keep
  it live from `Sync`. Reconcile periodically (§6).

**V3 (`UniswapV3Pool` and Slipstream pools):**
- Subscribe to `Swap(...,uint160 sqrtPriceX96,uint128 liquidity,int24 tick)`,
  `Mint`, and `Burn`.
- `Swap` carries `sqrtPriceX96`, `liquidity`, `tick` post-trade → write straight
  into the mirror.
- `Mint`/`Burn` change in-range `liquidity` without a `Swap`; when the modified
  range brackets the current tick, refresh `liquidity` (from the event delta or a
  `liquidity()` read). Seed `slot0()`+`liquidity()` via multicall at startup.

The engine prices a single active tick with `sqrt_price_x96`/`tick`/`liquidity`;
we do **not** need to ship the full tick map for 2-hop/triangular detection. (If
the engine later wants depth beyond the active tick, that is a new milestone, not
a silent change here.)

---

## 4. V4 ingestion (Unichain-critical) — singleton → `v3` shape

Unichain's liquidity is **Uniswap V4**, and V4 is spreading to the other chains.
V4 has no per-pool contract: one `PoolManager` singleton holds every pool's state,
keyed by `poolId = keccak256(abi.encode(PoolKey))` where
`PoolKey = { currency0, currency1, uint24 fee, int24 tickSpacing, address hooks }`.

The core concentrated-liquidity math is **identical to V3**, so a V4 pool maps
cleanly onto the engine's `v3` shape — the only differences are *how we read it*
and *how we identify it*:

| Concern | V3 | V4 |
|---------|----|----|
| Identity (`address`) | pool contract address | `poolId` rendered as `0x`+64 hex |
| State read | `slot0()`/`liquidity()` on the pool | `StateView.getSlot0(id)` / `getLiquidity(id)`, or `PoolManager.extsload` |
| Live updates | pool `Swap`/`Mint`/`Burn` | `PoolManager.Swap(id,…,sqrtPriceX96,liquidity,tick,fee)` / `ModifyLiquidity(id,…)` |
| Fee | static `fee()` | static in `PoolKey.fee`, **or dynamic** if `fee == 0x800000` (read live) |

Adapter rules:
1. Subscribe to `PoolManager` logs **filtered by the `poolId` topics** of the
   pools in the registry — one subscription per chain covers all its V4 pools.
2. Decode `Swap` → `sqrt_price_x96`, `liquidity`, `tick` (post-swap) → emit as
   `kind:"v3"`. Reconcile against `StateView.getSlot0(id)` (§6).
3. `address` = the `poolId`. Keep the full `PoolKey` in the pool registry so the
   downstream **execution** component (out of scope here) can reconstruct the
   route; detection only needs the identity + state.
4. **Hook safety gate (mandatory):** only ingest a V4 pool if its `hooks` address
   is `0x0` **or** on the per-chain `safe_hooks` allow-list. Any other hook can
   alter swap accounting and would make our `v3` pricing wrong. Reject with a
   loud log otherwise.
5. **Dynamic fee:** if `PoolKey.fee == 0x800000`, the fee is set by the hook per
   swap. Read the effective fee for the stamped block (from the `Swap` event's
   `fee` field, which V4 emits). If it cannot be pinned to the block, the pool is
   `verified:false` and cannot drive a reported opp.

---

## 5. Block-stamping and the "in sync" question

**You cannot put five heterogeneous chains on one global block number** — they
have independent clocks and block times. "In sync" here means two precise things,
both enforced:

1. **Intra-chain consistency:** every pool from chain C in a single
   `DetectRequest` carries the *same* `blockstamp` (the chain's latest verified
   block). We never mix block N and block N−1 state for the same chain in one
   request. The aggregator snapshots a chain atomically.
2. **Freshest-verified-per-chain:** each chain contributes its newest verified
   block. Cross-chain opportunities are priced from each leg's own blockstamp; the
   engine's `settle_seconds`/risk model already accounts for the time skew between
   chains, so this is correct, not a compromise.

`blockstamp` = `{ chain_id, number, block_hash, timestamp }`. The `block_hash`
is what makes a value *verifiable and reorg-aware*: it pins the state to a
specific canonical block, not just a height.

---

## 6. `verified` — the honesty flag

`verified:true` means: *this exact state is reproducible from the canonical chain
at `blockstamp.block_hash`.* Set it true only when **both** hold:

- The state was derived from logs/reads under a block whose hash we have
  confirmed is on the canonical chain (not an orphaned/reorged block), **and**
- The most recent **reconciliation** for this pool passed: an independent
  `eth_call` (`getReserves`/`slot0`+`liquidity`, or `StateView` for V4) at a
  pinned block equals the event-derived mirror value **exactly**.

Set `verified:false` (and keep emitting it, so consumers see the gap) when: a
reorg is in flight for that chain, the WebSocket just reconnected and the mirror
is re-seeding, a reconciliation mismatch was detected, or a dynamic fee/hook
value could not be pinned. The engine reports opportunities with `verified` on
each pool; a downstream executor must ignore any opp whose legs aren't all
verified. **We never fabricate `verified:true` to make a test pass.**

---

## 7. Per-chain gas & price context (`chains[]`)

Per chain we assemble:

```jsonc
{
  "chain_id": 8453,
  "gas_price_wei": <eth_gasPrice or baseFee+tip>,
  "l1_data_fee_wei": <see below>,
  "base_gas": 150000, "per_hop_gas": 100000, "gas_safety_multiplier": 1.5,  // from config
  "min_profit_bps": 5.0,                                                     // from config
  "native_price_in": { "<token>": <numeraire-base-units per 1 wei of native>, … },
  "hubs": ["0x…weth", "0x…usdc"]
}
```

**`gas_price_wei`** — L2 execution gas price. `eth_gasPrice`, or
`baseFeePerGas + priorityFee` from the latest header / `eth_feeHistory`.

**`l1_data_fee_wei`** — the L1 data-availability cost of landing the arb tx:
- **OP Stack chains (Base 8453, Optimism 10, Unichain 130, Ink 57073):** query the
  `GasPriceOracle` predeploy at `0x420000000000000000000000000000000000000F` —
  `getL1Fee(bytes)` on a representative serialized arb tx gives the current
  Ecotone/Fjord L1 fee directly. (Do not hand-roll the Fjord FastLZ formula; ask
  the oracle.) The `L1Block` predeploy `0x…15` exposes the underlying
  `l1BaseFee`/`blobBaseFee` if you want to model it.
- **Arbitrum (42161):** L1 calldata cost is folded into the gas *units* Nitro
  charges, so set `l1_data_fee_wei: 0` and let `gas_price_wei × gas` cover it —
  or, for precision, read the `ArbGasInfo` precompile
  `0x000000000000000000000000000000000000006C` (`getPricesInWei`,
  `getL1BaseFeeEstimate`) and model it explicitly. Default to `0` + a slightly
  higher `gas_safety_multiplier`; make it configurable.

**`native_price_in[T]`** — "numeraire-base-units of token `T` per 1 wei of the
native gas token (ETH)." Derive it **from the pools we already ingest**, which is
elegant and self-consistent:

```
price_native_in_T  = spot price of WETH quoted in T, in human units   // from the live WETH/T pool
native_price_in[T] = price_native_in_T × 10^(T.decimals) / 10^18
```

- For `T = WETH` (18 dec) this is exactly `1.0`.
- For `T = USDC` (6 dec) at 1 ETH = 1873 USDC:
  `1873 × 10^6 / 10^18 = 1.873e-9`.
- A numeraire with **no** derivable native price path MUST be omitted — the engine
  cannot gas-cost it and will never report it. So ensure every configured hub /
  numeraire has a WETH/T (or multi-hop) price route in the ingested set, and log
  any that don't.

**`hubs`** — the numeraire/base tokens to route through (WETH, USDC, USDT, …).
Optional (engine falls back to busiest tokens), but set them explicitly per chain
for determinism.

---

## 8. `cross_chain` block

Optional, but it's what unlocks cross-chain 2-hop detection across our five
chains. Assembled from config + the token registry:

- **`assets[]`**: one canonical symbol (e.g. `"WETH"`) → its per-chain
  `representations` (`{ token:{chain_id,address,decimals}, native, bridgeable }`).
  `native:true` = canonical/native representation on that chain (not a wrapped
  bridge asset); `bridgeable:true` = a configured bridge can move it.
- **`bridges[]`**: `{ symbol, from_chain, to_chain, fee_bps, fixed_fee,
  settle_seconds }` — the real economics of each supported bridge route. These are
  config, sourced from the bridge's docs and kept current; they are *not*
  on-chain-derivable, so they live in `config` and are reviewed, not synthesized.
- **`pairs[]`**: `[[asset, numeraire], …]` canonical-symbol pairs to scan, e.g.
  `[["WETH","USDC"]]`.

Represent only assets that have a genuine same-asset representation on ≥2 of our
chains and a real bridge between them. Do not invent bridge routes.

---

## 9. Transport & cadence

- **Default transport: HTTP `POST /detect`** to a **long-running** `l2arb`
  FastAPI service, over a **keep-alive connection pool** (do not spawn a process
  per call; do not `uvicorn` per call — start it once). Lowest-friction,
  plug-and-play, and localhost keep-alive latency is sub-millisecond to low-ms.
- **Alternative: subprocess** `python -m l2arb.api.runner` (one request → one
  response → exit). Fine for batch/CLI use; **not** for the high-frequency hot
  path because of per-call process spawn cost. Support it behind the same
  `EngineClient` trait for portability, but default to HTTP.
- **Health:** gate startup and reconnect on `GET /health → {"status":"ok"}`.
- **Cadence:** build+send a `DetectRequest` on *meaningful change* (a tracked
  pool updated) with a debounce floor (`min_interval_ms`) and a heartbeat ceiling
  (`max_interval_ms`). Use `"incremental": true` after the first full request so
  the engine only re-scans pools that changed since the last call — this is the
  throughput lever. Send the first request of a session with
  `"incremental": false`.

---

## 10. Response handling

Parse `{ count, opportunities[] }`. For each opportunity: confirm every leg's
pool was `verified:true` in the request we sent, confirm `net_profit > 0` and the
`block` stamps are the ones we sent, then forward `{snapshot_meta, opportunity}`
to the **output sink** for the GUI + execution engine. Never mutate the engine's
ranking; it already ordered by risk-adjusted `score`. Treat a non-200 / exit-1 /
schema-invalid response as a failed tick: log, count the error metric, retry with
backoff, and keep the last good snapshot available — do not crash the ingestor.
