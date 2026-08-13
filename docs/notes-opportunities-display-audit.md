# Granular audit — "why do no opportunities display?"

Branch `claude/opportunities-display-audit-81ld6i`, 2026-08-13.

Task, verbatim: *"Run a granular audit to find out why no opportunities display."*

This is an investigation-first pass: trace the opportunity from on-chain state to
the dashboard row and find every place one can be dropped. Each finding below was
reproduced before being written down, and each claim is backed by something that
was actually executed — the real Python engine, the real `l2-ingest` binary, real
`eth_call`s against live chains — not by reading alone. One hypothesis was
disproved that way and is recorded as such (§5).

---

## 1. The answer in one paragraph

**Paper mode is fine; the live path is dark, and it is dark before the dashboard
ever sees anything.** A `config.toml` that has real RPC endpoints but still carries
the shipped `"0xWETH"` / `"0xWETH_USDC_POOL"` token placeholders — which is exactly
what `docker-compose.yml`'s quick start tells an operator to produce — resolves to
an **empty `native_price_in` map**. The detection engine cannot gas-cost a numeraire
it has no native price for, so it charges every one of them an effectively infinite
gas cost and **rejects 100% of the opportunities it finds**. Every layer reports
success throughout: `--check-config` printed `config OK`, `/health` stayed green,
the ingestion layer kept broadcasting envelopes, and the dashboard rendered an empty
table. Nothing anywhere logged a reason.

---

## 2. F1 (CRITICAL, root cause) — the silent zero-detection config

### The causal chain, link by link

1. **`ingestion/config/config.example.toml`** ships every one of the 5 chains with
   `hubs = ["0xWETH", "0xUSDC", "0xUSDT"]`, `numeraires = [...]`, `weth = "0xWETH"`,
   and `[chains.native_price_pools] "0xUSDC" = "0xWETH_USDC_POOL"`. Root `CLAUDE.md`
   §16 already recorded these as deliberately-unfilled ("Not done, deliberately"),
   but not that leaving them unfilled is *silently fatal*.

2. **`l2i_config::Config::validate()`** checked `ws_url`/`http_url` for the
   `YOUR_` placeholder marker (added in §17) but **never looked at the token
   fields**. So filling in only the endpoints was enough to pass the gate.

3. **`crates/app/src/context.rs::derive_native_prices`** — every parse is a silent
   discard:
   - `cfg.weth` (`"0xWETH"`) → `Address::from_str(..).ok()` → `None`
   - each `native_price_pools` key (`"0xUSDC"`) → `let Ok(..) else { continue }`
   - each value (`"0xWETH_USDC_POOL"`) → `let Some(..) else { continue }`

   `entries` ends up empty and `weth` is `None`, so the final
   `match weth { None => BTreeMap::new(), .. }` returns an **empty map**. Note the
   ordering trap: when `weth` *is* set, `build_native_price_map` seeds it at `1.0`,
   so WETH-numeraire opportunities survive even with no pricing pools — it is the
   *unparseable* `weth` specifically that collapses the map to nothing.

4. **Hubs, same file:** `cfg.hubs.iter().filter_map(|s| s.parse().ok())` → `[]`,
   with no log at all. `l2i_gas::assemble_chain_context` does warn about a hub with
   no native price — but it iterates the *surviving* hubs, so when every entry is
   dropped the loop body never executes and even that warning stays silent.

5. **`engine/src/l2arb/api/service.py::_gas_cost_fn`** closes it:

   ```python
   _UNPRICED_GAS = 10**36  # numeraire with no on-chain price -> reject the opportunity
   ...
   price = prices.get(numeraire[1], default)   # {} and default=None
   if price is None:
       return _UNPRICED_GAS
   ```

   Rejecting an unpriceable numeraire is *correct* — the engine must never invent a
   price. Doing it silently is what made a misconfiguration indistinguishable from a
   quiet market.

6. `/detect` returns zero opportunities → the ingestion layer broadcasts envelopes
   with an empty `opportunities` array → `ExternalProvider` maps nothing →
   the dashboard table is empty.

### Evidence

**(a) The real engine, A/B.** `scripts` under the session scratchpad built one
genuinely profitable 2-hop WETH/USDC pool pair (4% dislocation) and ran the real
`l2arb.api.service.detect` over it twice with byte-identical pools, gas, and
thresholds — the *only* difference being `native_price_in`:

```
A) native_price_in = {}                (shipped placeholders): 0 opportunities
B) native_price_in = {USDC: 3e-9}      (filled in):            1 opportunities
       strategy=two_hop numeraire=USDC profit_bps=192.94 net_profit=5588047895 gas_cost=19440
```

A 192-bps opportunity — one that is not remotely marginal — is discarded outright.

**(b) The real binary said the broken config was fine.** `cargo build --release
--bin l2-ingest`, then `--check-config` against `config.example.toml` with *only*
the endpoints substituted:

```
l2-ingest config OK (schema_version 1)
  chains (5 enabled):
    - arbitrum  chain_id=42161 ... enabled=true
    ...
  cross_chain: enabled=true status=usable
EXIT CODE: 0
```

Every chain "enabled", cross-chain "usable", exit 0 — for a config that detects
nothing, ever.

**(c) The repo's own test asserted the bug was correct.** `crates/config`
carried:

```rust
#[test]
fn example_config_is_structurally_valid_once_endpoints_are_filled() {
    example_live_ready().validate()
        .expect("config.example.toml is structurally valid once endpoints are real");
}
```

`example_live_ready()` substituted endpoints only. The test was right that the
config is *structurally* valid; it encoded as correct the very state that is
*semantically* inert. This is the same shape as §11 item 3 ("a pre-existing test
had encoded the bug as correct behavior") and was **corrected, not deleted**.

**(d) The documented path leads straight into it.** `docker-compose.yml`'s quick
start says `cp ingestion/config/config.example.toml ingestion/config.toml` then
"edit: real RPC ws/http endpoints per chain, curated pools", and sets
`DATA_SOURCE: "external"` unconditionally. Its own note — "Until
ingestion/config.toml has real endpoints, the engine + ingestion run but emit no
opportunities" — implies endpoints are the sufficient condition. They are not.

### Why the launcher masked it

`launcher/l2arb/config.py::config_is_live_ready()` *does* catch this, via the
non-hex-`0x`-token heuristic added in §15 item 10, and downgrades to paper mode.
So an operator going through `l2arb run` sees simulated opportunities rather than
an empty table — which is why this survived four prior audits. The exposed paths
are the ones that bypass the Python launcher: `docker compose up`, and running
`./target/release/l2-ingest --config config.toml` directly as this component's own
`CLAUDE.md` §6 documents.

### Fixed

- **`crates/config/src/lib.rs`** — `validate()` now rejects, for every *enabled*
  chain, an unparseable `hubs` / `numeraires` / `weth` entry, an unparseable
  `native_price_pools` key or value (accepting both a 20-byte pool address and a
  32-byte V4 `poolId`, mirroring `parse_pool_identity`), and a chain that has
  neither a `weth` nor any pricing pool — i.e. one that can price gas in no
  numeraire at all. Each error names the chain, the field, and the offending value,
  and points at `l2arb setup`.
- **`crates/app/src/context.rs`** — the silent drops are now loud: `parse_hubs()`
  warns per discarded hub and escalates to `error!` when every one is dropped;
  `derive_native_prices` warns per unusable `native_price_pools` entry and
  `error!`s on an unparseable `weth`; `build_chain_context` `error!`s when the
  derived `native_price_in` is empty, stating the consequence explicitly ("will
  report ZERO opportunities"). This is defence in depth for any path that reaches
  the runtime without validating.
- **`engine/src/l2arb/api/service.py`** — `_gas_cost_fn` logs once per chain per
  request when `native_price_in` is empty *and* `default_native_price` is unset,
  naming the caller-side config that produces it. Logged at build time, not inside
  `cost()`, which is on the hot path.
- **Docs** — `docker-compose.yml`'s quick start now lists every field that must be
  filled and tells the operator to run `--check-config` first;
  `config.example.toml` carries a prominent block explaining that the token fields
  are placeholders too, what breaks if they are left, and that `l2arb setup`
  generates them for real.

### Proof the fix closes it

Same binary, rebuilt, same endpoints-only config:

```
config invalid: invalid config: chain 'arbitrum' hub '0xWETH' is not a 20-byte
address — fill in the real token address (the shipped example ships '0xWETH'-style
placeholders; see config/config.example.toml, or run `l2arb setup`)
EXIT CODE: 1
```

And the working paths are unaffected — a fully-filled config validates (`exit 0`,
5 chains enabled), as do both real configs the launcher generates
(`setup.arbitrum_quickstart_config` and `setup.render_chain_block` via
`--all-chains`), checked by feeding their actual output to the real binary. The
Unichain V4 `poolId` placeholder (`"0xWETH_USDC_V4_POOLID"`) is caught too — the
same field family §15 item 10 found the launcher's own checker was missing.

---

## 3. Secondary findings — dashboard display filters (reported, not changed)

These are all **visible, operator-adjustable settings**, not silent failures, so
they are recorded here with recommendations rather than silently retuned; the
defaults encode risk choices that are the operator's to make. Each was measured by
running real mapped engine data through the actual `ArbitrageEngine.qualifies()`
funnel.

| # | Finding | Measured effect |
|---|---------|-----------------|
| **F2** | `networks` defaults to `["base", "arbitrum"]` (`settings/schema.ts`), but the ingestion layer ships **all five** chains `enabled = true`. | An Optimism opportunity is **DROPPED** under defaults. Optimism has by far the largest shipped registry — **22** on-chain-verified pools, vs Arbitrum's 4 and Base's 4 — so the richest data source is filtered out before display. |
| **F3** | `minProfitBps` defaults to `8`; every shipped chain's `min_profit_bps` is `5.0`. | The engine emits 5–8 bps opportunities the dashboard then discards. A 6-bps opportunity is **DROPPED**. |
| **F4** | `minProfitUsd` defaults to `25`, applied to USD-denominated (stablecoin-numeraire) opportunities. | Real L2 arbitrage nets are frequently single-digit dollars; this alone can empty the table on a working feed. Correctly skipped for non-USD numeraires (`numeraireIsUsd === false`). |

**Recommended first move for an operator seeing an empty table on a live feed:**
enable all five network chips, drop `minProfitBps` to 5 to match the engine, and
drop `minProfitUsd` to 0 to see what the feed is actually producing before
re-tightening.

---

## 4. Further findings (recorded, not fixed)

- **F5 — `l2arb setup --all-chains` can write an enabled chain with no
  `native_price_pools`.** `_pick_native_price_pool(discovery, ...)` returns `None`
  when live pool discovery yields nothing and the wizard falls back to the shipped
  example registry — a real, common path (§17 recorded Arbitrum taking it when
  rate-limited). The chain is still written enabled with a real `weth`, so
  WETH-numeraire opportunities survive at `native_price_in = {WETH: 1.0}`, but every
  **USDC-numeraire** opportunity on it is silently rejected. A partial F1. The clean
  fix is to derive the pricing pool from the materialised registry file (which does
  contain a real WETH/USDC pool) rather than only from the live-discovery result;
  left unfixed because it changes wizard behaviour on a path whose end-to-end
  correctness deserves a live re-run of `--all-chains`, not a same-pass edit.
  The new `error!` on an empty `native_price_in` at least makes it self-announcing.
- **F6 — `ink.example.toml` ships exactly one pool.** A single pool cannot close a
  cycle, so Ink can never produce a same-chain opportunity regardless of
  configuration. Honest (§16 notes Ink genuinely has one real WETH/USDC pool today),
  but worth stating plainly rather than leaving an operator to wonder.
- The `--check-config` summary prints `chains (5 enabled)` with no indication of how
  many pools or priceable numeraires each chain actually has. Adding those counts
  would have made F1 obvious at a glance; a natural follow-up now that the
  underlying data is validated.

---

## 5. A hypothesis that was wrong — recorded deliberately

Mid-audit the leading theory for the dashboard-side drop was that
`ArbitrageEngine.qualifies()` requires **both** tokens of **every** leg to be in
`tokens ∪ {baseToken}` (default `["USDC","WETH","USDT","DAI","WBTC"]`), and that
Arbitrum's bridged USDC pool — present in the shipped registry and commented
`# USDC.e (bridged)` — would surface as the symbol `"USDC.e"` and be dropped. A
probe confirmed such an opportunity *would* be dropped.

The premise was false. The engine labels a leg with the symbol the ingestion
validation gate reads on-chain via ERC-20 `symbol()`, and a live `eth_call` against
`0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8` on Arbitrum returns:

```
0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8 -> 'USDC'
```

`.e` is an explorer/UI convention, not the contract's symbol. Reading every token
across all five shipped registries the same way returns only `WETH`, `USDC`, `USDT`
and `DAI` — all four already in the default allowlist. **The token allowlist is not
a cause.** Recorded so a future session doesn't re-derive the same wrong lead from
the registry comments.

---

## 6. Confirmed *not* the cause

- **Paper/simulated mode works.** Driving the real `SimulatedProvider` through the
  real `ArbitrageEngine` at stock defaults surfaced **158 opportunities over 200
  ticks**. An empty table in paper mode is a different problem from this one.
- **The display layer is a pass-through.** `OpportunitiesTable.tsx` sorts by net
  profit and slices the top 18; `liveReducer.ts` dedupes by id and caps the list.
  Neither filters, so nothing is lost between the API and the screen.
- **The `qualifies()` defects from §9 and §11 are genuinely fixed and still hold** —
  re-verified by composing `engineMap → qualifies()` directly: a pool-address-labelled
  leg is not venue-filtered, and a WETH-numeraire opportunity qualifies under a
  `baseToken = "USDC"` default.

---

## 7. Gates

Run directly by this session, before and after the changes:

| Component | Result |
|-----------|--------|
| ingestion | `cargo fmt --all --check` clean, `clippy --all-targets --all-features -D warnings` clean, `cargo test --workspace` **225 passed** (was 222; +3 new config tests) |
| engine | `make check` **469 passed**, coverage 99.87% (floor 85%) |
| launcher | `python3 -m unittest discover -s launcher/tests` **276 passed** |
| dashboard | `pnpm verify` (typecheck + test + build) exit 0 — **131 backend + 67 frontend passed**; unchanged by this pass, re-run green |
| contracts | untouched, not re-run |

New permanent tests in `crates/config`: `filling_only_the_endpoints_is_not_enough_to_pass_validation`
(the corrected assertion), `rejects_placeholder_numeraire_weth_and_native_price_pool`
(each field independently, plus the accepting cases for a real pool address and a
real V4 poolId), and `rejects_a_chain_that_can_price_gas_in_no_numeraire`.
