# Learnings (accumulated gotchas)

Durable, hard-won facts the next iteration should NOT rediscover. Add an entry
whenever something surprised you or cost time. Keep each to a few lines. Cite a
file/section if relevant.

Format:
```
- [<area>] <the gotcha> → <what to do about it>
```

---

## Seeded domain knowledge (verify before relying on specifics)

- [amm/units] On-chain amounts are integers in token base units; `decimals`
  varies (USDC=6, WETH=18, WBTC=8). NEVER assume 18. Read decimals on-chain and
  carry them on `Token`. Unit confusion is the #1 pricing bug.
- [amm/v2] Implement the fee as an exact integer ratio (e.g. 997/1000) to match
  the contract bit-for-bit; float fees drift from on-chain results.
- [amm/v3] Do not trust hand-rolled tick-crossing until it matches on-chain
  QuoterV2 within ≤1 wei at pinned blocks. Use the quoter as the oracle of record;
  local math is for latency.
- [tokens] Fee-on-transfer / rebasing tokens break constant-product assumptions —
  quarantine them (see docs/SECURITY.md §3), don't let them into the graph.
- [xchain] "USDC" is not one asset: native USDC vs bridged USDC.e have different
  addresses and risk. Cross-chain fungibility must be on-chain-verified, never
  assumed 1:1 (docs/ARBITRAGE_THEORY.md §5).
- [graph] Marginal rates ignore size; a margin-profitable cycle can lose money
  after slippage+gas. The graph is a candidate generator; always re-price exactly.
- [latency] Warm up `numba` JIT at startup, or the first event pays compilation
  time and blows the SLO (docs/LATENCY.md §3).
- [data] Two independent sources must agree at the same block before a pool is
  "verified"; one RPC can lag or be misconfigured (docs/DATA_INTEGRITY.md §2).
- [tooling] Standard on `uv`; run everything via `uv run ...` / `make` targets so
  the environment is reproducible.
- [tooling/ruff] The repo's ruff config enforces: `@pytest.mark.parametrize`
  first arg must be a **tuple** of names (`("a", "b")` not `"a,b"`) (PT006);
  build dicts with `{...}` literals not `dict(...)` (C408); avoid the Unicode
  minus `−` (U+2212) in docstrings/comments — use ASCII `-` (RUF002). Run
  `make fmt` (format + `ruff check --fix`) before `make check` to catch these.
- [tooling/mypy] `int ** int` is typed `Any` in typeshed (negative exponents
  yield float), so `10 ** decimals` trips `no-any-return`. Wrap in `int(...)`
  when the exponent is known non-negative.
- [latency] The size solver evaluates a cycle's route dozens of times per
  candidate. Compiling the route once (`detect/profit._compile_route`: hoist
  per-hop oriented reserves/direction/fee out of the loop, no `Leg` allocation
  during search) cut full-sweep compute ~3.7x (232ms→63ms on 24 tokens/70 pools).
  Build `Leg` objects only once at the final optimal size. Next lever: numba on
  the tropical min-plus sweep + a closed-form fast path for 2-V2-hop sizing.
- [model/fees] Unified fee representation is `fee_pips` (millionths):
  `fee = fee_pips / 1_000_000`. 0.30 %→3000 reproduces V2's exact `997/1000`;
  V3 fee tiers (100/500/3000/10000) are already in these units. One convention
  for both AMM families keeps the exact-integer math uniform.
- [verify/blockscout] Blockscout MCP `read_contract` decodes large uints through
  JSON floats — **lossy above 2**53**. It returned a V2 `reserve0` as
  `...284200000` when the true value was `...284218593` (off by 18593), a V3
  `sqrtPriceX96` off by ~2.5e11, and router outputs off by 1–590 wei. For
  **exact** on-chain integers, use raw `eth_call` via `direct_api_call`
  (`endpoint_path='/json-rpc'`, POST) and decode the hex yourself (`eth_abi` is
  available for calldata encoding). The captured verify fixtures in
  `tests/verify/fixtures/` are built from hex, not that path.
- [amm/weighted] Balancer out-given-in via `Decimal` can **overstate at the
  extreme**: a very skewed weight ratio (e.g. 29:1) plus a huge input makes
  `power = (wIn/wOut · ln base).exp()` underflow the 60-digit precision to 0, so
  `balanceOut·(1-power)` rounds up to the *entire* out-balance — an impossible
  full drain. Clamp to `balance_out - 1` (the true floor there), matching V2's
  structural `< reserve_out` and StableSwap's `-1`. Found by the adversarial
  stress property test; pinned in `tests/amm/test_weighted.py`.
- [latency/sizing] Golden-section search over *integers* must NOT carry the two
  interior probes as coordinates across iterations. The float self-similarity
  `new_c == old_d` holds only approximately once rounded, so the carried probes
  drift and eventually cross (`c > d`), inverting the bracket → `max()` over an
  empty range. Fix: recompute both probes fresh from the live `[a,b]` each step
  (at width > 4 a fresh split keeps `c < d` provably), and recover the "reuse one
  eval" speedup with a **memo dict** — the reused probe becomes a cache hit, not a
  drifting coordinate. Robust to weakly-concave *plateaus* too (integer AMM profit
  is flat at the peak): on `f(c)==f(d)` narrow from the right; the maximizer
  interval is always retained. A strictly-concave property test alone won't catch
  the drift — it needs a wide-interval concave-with-plateau case (the two-V2-hop
  route in `test_never_reports_a_net_loss` is what surfaced it).
