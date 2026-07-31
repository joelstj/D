import { describe, it, expect } from "vitest";
import { SettingsStore } from "../src/settings/store";
import { ArbitrageEngine } from "../src/arbitrage/engine";
import { mapEngineOpportunity, type EngineOpportunity } from "../src/arbitrage/providers/engineMap";
import { makeOpportunity, StubProvider, pairExecutors } from "./helpers";

/**
 * Regression coverage for the seam that the unit suites never composed:
 * `engineMap` (what ExternalProvider — the real production feed — produces) run
 * through `ArbitrageEngine.qualifies()`. Before the fix, `qualifies()` compared
 * each leg's `dex` (a pool address, for engine data) against the venue-key chip
 * set, so *every* external opportunity was silently dropped and the live
 * dashboard was permanently empty.
 */

/** A minimal-but-valid engine opportunity on Base (chain 8453), USDC numeraire. */
function engineOpp(over: Partial<EngineOpportunity> = {}): EngineOpportunity {
  const usdc = { chain_id: 8453, address: "0xUSDC", decimals: 6, symbol: "USDC" };
  const weth = { chain_id: 8453, address: "0xWETH", decimals: 18, symbol: "WETH" };
  return {
    strategy: "two_hop",
    numeraire: usdc,
    input_amount: "10000000000", // 10,000 USDC (6dp)
    output_amount: "10050000000",
    gross_profit: "60000000", // 60 USDC
    gas_cost: "10000000", // 10 USDC
    bridge_cost: "0",
    net_profit: "50000000", // 50 USDC
    profit_bps: 50,
    expected_net: "45000000",
    score: 45,
    hops: 2,
    chain_ids: [8453],
    is_cross_chain: false,
    settle_seconds: 2,
    verified: true,
    block: { chain_id: 8453, number: 100, hash: "0xabc", timestamp: 1_700_000_000 },
    risk: { success_probability: 0.9, capture_ratio: 0.8, frontrun_risk: 0.1, notes: [] },
    legs: [
      {
        pool: "0x1111111111111111111111111111111111111111",
        token_in: usdc,
        token_out: weth,
        amount_in: "10000000000",
        amount_out: "3000000000000000000",
      },
      {
        pool: "0x2222222222222222222222222222222222222222",
        token_in: weth,
        token_out: usdc,
        amount_in: "3000000000000000000",
        amount_out: "10050000000",
      },
    ],
    ...over,
  };
}

describe("ExternalProvider opportunities survive qualifies() (regression)", () => {
  it("a mapped engine opportunity qualifies under DEFAULT settings (was dropped: pool-address vs venue-key)", async () => {
    const store = new SettingsStore(); // all defaults — the shipped config
    const mapped = mapEngineOpportunity(engineOpp());
    const engine = new ArbitrageEngine(store, new StubProvider([mapped]), pairExecutors());

    await engine.tick();
    // Before the fix this was 0 (every external leg's pool-address `dex` failed
    // the venue chip). It must now surface.
    expect(engine.getOpportunities()).toHaveLength(1);
  });

  it("a pool-address (unlabelled) leg is not subject to the venue chip, even when chips are narrowed", async () => {
    // Only uniswap-v3 enabled, yet the engine leg carries a pool address, not a
    // venue — the venue chip cannot honestly filter it, so it still qualifies.
    const store = new SettingsStore({ dexes: ["uniswap-v3"] });
    const mapped = mapEngineOpportunity(engineOpp());
    const engine = new ArbitrageEngine(store, new StubProvider([mapped]), pairExecutors());

    await engine.tick();
    expect(engine.getOpportunities()).toHaveLength(1);
  });

  it("still rejects an external opportunity on a disabled network / token (real engine data)", async () => {
    const store = new SettingsStore({ networks: ["arbitrum"] }); // base disabled
    const mapped = mapEngineOpportunity(engineOpp()); // base opp
    const engine = new ArbitrageEngine(store, new StubProvider([mapped]), pairExecutors());

    await engine.tick();
    expect(engine.getOpportunities()).toHaveLength(0);
  });

  it("a venue-labelled (simulated) leg IS still filtered by the venue chip", async () => {
    // The makeOpportunity fixture's second leg is "aerodrome" (a known venue key),
    // excluded here — so it must be dropped. Proves the chip still works where the
    // data supports it.
    const store = new SettingsStore({ dexes: ["uniswap-v3"], minProfitUsd: 0, minProfitBps: 0 });
    const engine = new ArbitrageEngine(store, new StubProvider([makeOpportunity()]), pairExecutors());

    await engine.tick();
    expect(engine.getOpportunities()).toHaveLength(0);
  });
});

describe("USD-magnitude gates apply only to USD-denominated opportunities", () => {
  it("a non-USD numeraire opp below minProfitUsd still qualifies on bps (no base-units-vs-dollars compare)", async () => {
    const store = new SettingsStore({
      baseToken: "WETH",
      minProfitUsd: 25, // would reject a "netProfitUsd" of 5...
      minProfitBps: 8,
      maxPositionUsd: 1, // ...and this would reject amountInUsd of 50000...
    });
    // ...but numeraireIsUsd:false means those figures are WETH base units, so the
    // USD gates are skipped and the unit-agnostic bps gate (50 >= 8) governs.
    const opp = makeOpportunity({
      tokenIn: "WETH",
      numeraireIsUsd: false,
      netProfitUsd: 5,
      amountInUsd: 50_000,
      profitBps: 50,
    });
    const engine = new ArbitrageEngine(store, new StubProvider([opp]), pairExecutors());

    await engine.tick();
    expect(engine.getOpportunities()).toHaveLength(1);
  });

  it("a USD-denominated opp with the same numbers IS rejected by minProfitUsd", async () => {
    const store = new SettingsStore({ minProfitUsd: 25, minProfitBps: 8 });
    const opp = makeOpportunity({ numeraireIsUsd: true, netProfitUsd: 5, profitBps: 50 });
    const engine = new ArbitrageEngine(store, new StubProvider([opp]), pairExecutors());

    await engine.tick();
    expect(engine.getOpportunities()).toHaveLength(0);
  });
});

describe("maxDailyLossUsd=0 does not halt execution before any loss", () => {
  it("permits a manual execute at t0 when maxDailyLossUsd=0 and no loss yet", async () => {
    const store = new SettingsStore({ minProfitUsd: 0, minProfitBps: 0, maxDailyLossUsd: 0 });
    const engine = new ArbitrageEngine(
      store,
      new StubProvider([makeOpportunity({ netProfitUsd: 175 })]),
      pairExecutors(),
    );
    await engine.tick();
    const opp = engine.getOpportunities()[0]!;

    // Was: `dailyPnlUsd (0) <= -0` tripped at t0. Now strict `<` lets it through.
    await expect(engine.executeOpportunity(opp.id)).resolves.toBeDefined();
  });
});
