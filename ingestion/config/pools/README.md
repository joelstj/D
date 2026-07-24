# Per-chain pool registries

One file per chain (e.g. `arbitrum.toml`, `base.toml`, `unichain.toml`). Each
lists the pools that chain's ingestor tracks. **Every entry is validated
on-chain at startup** (contract exists, tokens/fee/decimals match, factory known,
V4 hook on the safe-list) before it enters the live set — see
`docs/ARCHITECTURE.md §7`. Invalid entries are rejected loudly, never silently
dropped.

Only pool types the engine can price may be listed: constant-product (`v2`),
Uniswap-V3-style concentrated liquidity (`v3`), and Uniswap **V4** pools with a
`0x0` or safe-list hook (mapped onto the `v3` shape). Stableswap / Solidly-stable
/ weighted pools are **out of scope** (`docs/ENGINE_CONTRACT.md §1`).

Pool addresses are intentionally **not** committed as a curated set here — they
are environment/DEX-specific and must be gathered and verified for your
deployment. Populate these files (a discovery script can seed them from factory
`PairCreated`/`PoolCreated` events filtered by your token allow-list and a
liquidity floor), then let the startup gate prove each one.

## Schema

```toml
# V2 (constant product) — Uniswap V2 family / Solidly *volatile* pools
[[pool]]
dex      = "uniswap_v2"      # informational; factory is verified on-chain
kind     = "v2"
address  = "0x…"             # the pair contract
fee_pips = 3000              # 0.30% (in millionths)
token0   = "0x…"             # must equal on-chain token0() (canonical ordering)
token1   = "0x…"

# V3 (concentrated liquidity) — Uniswap V3 family / Slipstream
[[pool]]
dex      = "uniswap_v3"
kind     = "v3"
address  = "0x…"
fee_pips = 500               # 0.05%
token0   = "0x…"
token1   = "0x…"

# V4 (singleton) — identity is the poolId; the full PoolKey is retained so the
# downstream executor can reconstruct the route. hooks must be 0x0 or on the
# chain's safe_hooks allow-list.
[[pool]]
dex          = "uniswap_v4"
kind         = "v4"                 # ingested/emitted to the engine as kind="v3"
id           = "0x<32-byte poolId>" # becomes the engine pool `address`
currency0    = "0x…"                # PoolKey
currency1    = "0x…"
fee          = 500                  # or 0x800000 for dynamic-fee (read live per block)
tick_spacing = 10
hooks        = "0x0000000000000000000000000000000000000000"
```

Notes:
- `token0`/`token1` (and `currency0`/`currency1`) must be in canonical byte
  order; the startup gate rejects mismatches.
- `fee_pips` must equal the on-chain fee; for V4 dynamic-fee pools the effective
  fee is read for each stamped block.
- Tokens referenced here must not be on the chain's fee-on-transfer/rebasing
  deny-list.
