# Integrating the calculation engine

`l2arb` is a **detection/calculation engine**: you feed it live, on-chain-verified
pool state (gathered by your per-chain bots) plus each chain's gas/price context,
and it returns the ranked **top-N arbitrage opportunities** — same-chain 2-hop,
triangular, cross-dex, bounded multi-hop, and cross-chain 2-hop. It holds no keys,
signs nothing, and submits no transactions.

Two language-agnostic transports share one JSON contract, so a Rust / Go /
TypeScript / Node / C# / C++ / JVM backend can drop it in with no Python bindings.

## 1. Subprocess (stdin → stdout) — zero setup

Spawn the runner, write one JSON request to stdin, read the JSON response from
stdout:

```bash
echo "$REQUEST_JSON" | python -m l2arb.api.runner
```

Exit code `0` with `{"count", "opportunities"}` on success; exit code `1` with
`{"error", "type"}` on a malformed request (the error is JSON too).

## 2. HTTP (read-only FastAPI)

```bash
uvicorn l2arb.api.http:app --port 8080
# POST /detect  -> ranked opportunities ; GET /health -> {"status":"ok"}
```

Both call the same `l2arb.api.service.detect`, so behaviour never diverges.

## 3. Request shape (`DetectRequest`)

```jsonc
{
  "top_n": 10,
  "max_hops": 4,               // 2..8
  "incremental": false,         // true = only re-scan pools changed since last call
  "chains": [
    {
      "chain_id": 42161,
      "gas_price_wei": 10000000,
      "l1_data_fee_wei": 0,
      "base_gas": 150000, "per_hop_gas": 100000, "gas_safety_multiplier": 1.5,
      "min_profit_bps": 5.0,
      // numeraire-base-units per 1 native gas-token wei, per token address.
      // A numeraire with no price here cannot be gas-costed and is never reported.
      "native_price_in": { "0x...weth": 1.0, "0x...usdc": 0.0000000003 },
      "hubs": ["0x...weth", "0x...usdc"]   // optional; else busiest tokens are used
    }
  ],
  "pools": [ /* pool objects — see below */ ],
  "cross_chain": {              // optional
    "assets": [
      { "symbol": "WETH", "representations": [
          { "token": { "chain_id": 42161, "address": "0x...", "decimals": 18 }, "native": true, "bridgeable": true },
          { "token": { "chain_id": 8453,  "address": "0x...", "decimals": 18 }, "native": true, "bridgeable": true }
      ] }
    ],
    "bridges": [
      { "symbol": "WETH", "from_chain": 42161, "to_chain": 8453, "fee_bps": 10.0, "fixed_fee": 0, "settle_seconds": 600 }
    ],
    "pairs": [["WETH", "USDC"]]  // (asset, numeraire) canonical symbols to scan
  }
}
```

### Pool object

Every pool is block-stamped. **Big integers are decimal strings** (reserves reach
2¹¹², `sqrtPriceX96` ~2¹⁶⁰ — beyond JSON's safe integer and many languages' native
ints), so nothing is lost across the boundary.

```jsonc
// constant-product (Uniswap V2 family)
{
  "address": "0x...", "kind": "v2", "fee_pips": 3000, "verified": true,
  "token0": { "chain_id": 42161, "address": "0x...", "decimals": 18, "symbol": "WETH" },
  "token1": { "chain_id": 42161, "address": "0x...", "decimals": 6,  "symbol": "USDC" },
  "blockstamp": { "chain_id": 42161, "number": 200000000, "block_hash": "0x...", "timestamp": 1752460000 },
  "v2": { "reserve0": "1234567890000000000000", "reserve1": "3210000000000" }
}

// concentrated liquidity (Uniswap V3 family): "kind":"v3" with
"v3": { "sqrt_price_x96": "79228162514264337593543950336", "tick": 0, "liquidity": "9876543210000000" }
```

`fee_pips` is the fee in millionths (0.30 % = 3000, 0.05 % = 500) — unified across
V2 and V3.

## 4. Response shape

```jsonc
{
  "count": 2,
  "opportunities": [
    {
      "strategy": "two_hop",            // two_hop | triangular | multi_hop | cross_chain_two_hop
      "numeraire": { "chain_id": 42161, "address": "0x...", "decimals": 18, "symbol": "WETH" },
      "input_amount": "…", "output_amount": "…",
      "gross_profit": "…", "gas_cost": "…", "bridge_cost": "0",
      "net_profit": "…",                // = gross - gas - bridge, always > 0 when reported
      "profit_bps": 42.7,
      "expected_net": "…",              // risk-adjusted (score is this as a float)
      "score": 1234.5,                  // ranking key (risk-adjusted expected value)
      "hops": 2, "chain_ids": [42161], "is_cross_chain": false, "settle_seconds": 0,
      "verified": true,
      "block": { "chain_id": 42161, "number": 200000000, "hash": "0x...", "timestamp": 1752460000 },
      "risk": { "success_probability": 0.9, "capture_ratio": 0.6, "frontrun_risk": 0.1, "notes": ["hops=2", "..."] },
      "legs": [
        { "pool": "0x...", "token_in": {…}, "token_out": {…}, "amount_in": "…", "amount_out": "…" }
      ]
    }
  ]
}
```

Opportunities are de-duplicated (same pools + numeraire) and ordered by `score`
(risk-adjusted expected value). Slippage is exact (inside the AMM math); gas is L2
execution + L1 data cost converted to the numeraire via your on-chain price;
`risk` models MEV competition and front-/back-running so a downstream executor can
prioritise the fills most likely to land. `net_profit` is never negative when an
opportunity is reported.
