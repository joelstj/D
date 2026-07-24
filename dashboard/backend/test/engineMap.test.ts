import { describe, it, expect } from "vitest";
import {
  mapEngineOpportunity,
  opportunityId,
  isUsdStable,
  type EngineOpportunity,
} from "../src/arbitrage/providers/engineMap";

/** A realistic 2-hop USDC-denominated detection on Base (all amounts base units). */
function sampleOpp(overrides: Partial<EngineOpportunity> = {}): EngineOpportunity {
  const usdc = { chain_id: 8453, address: "0xUSDC", decimals: 6, symbol: "USDC" };
  const weth = { chain_id: 8453, address: "0xWETH", decimals: 18, symbol: "WETH" };
  return {
    strategy: "two_hop",
    numeraire: usdc,
    input_amount: "100000000", // 100 USDC
    output_amount: "100500000", // 100.5 USDC
    gross_profit: "500000", // 0.50 USDC
    gas_cost: "120000", // 0.12 USDC
    bridge_cost: "0",
    net_profit: "380000", // 0.38 USDC
    profit_bps: 38,
    expected_net: "360000",
    score: 0.9,
    hops: 2,
    chain_ids: [8453],
    is_cross_chain: false,
    settle_seconds: 0,
    verified: true,
    block: { chain_id: 8453, number: 12345678, hash: "0xabc", timestamp: 1690000000 },
    risk: { success_probability: 0.85, capture_ratio: 0.8, frontrun_risk: 0.1, notes: [] },
    legs: [
      {
        pool: "0xPool1000000000000000000000000000000000001",
        token_in: usdc,
        token_out: weth,
        amount_in: "100000000",
        amount_out: "31250000000000000", // 0.03125 WETH
      },
      {
        pool: "0xPool2000000000000000000000000000000000002",
        token_in: weth,
        token_out: usdc,
        amount_in: "31250000000000000",
        amount_out: "100500000",
      },
    ],
    ...overrides,
  };
}

describe("mapEngineOpportunity", () => {
  it("scales stablecoin base units to USD figures exactly", () => {
    const o = mapEngineOpportunity(sampleOpp(), 1_700_000_000_000);
    expect(o.amountInUsd).toBeCloseTo(100, 9);
    expect(o.grossProfitUsd).toBeCloseTo(0.5, 9);
    expect(o.gasCostUsd).toBeCloseTo(0.12, 9);
    expect(o.netProfitUsd).toBeCloseTo(0.38, 9);
    expect(o.flashLoanFeeUsd).toBe(0); // detection engine models no flash-loan fee
    expect(o.profitBps).toBe(38);
    expect(o.spreadBps).toBe(38);
    expect(o.confidence).toBe(0.85);
  });

  it("maps chain id onto a known dashboard network", () => {
    const o = mapEngineOpportunity(sampleOpp(), 1_700_000_000_000);
    expect(o.network).toBe("base");
    expect(o.chainId).toBe(8453);
    expect(o.tokenIn).toBe("USDC");
  });

  it("falls back to chain-<id> for an unregistered chain", () => {
    const usdc = { chain_id: 99999, address: "0xUSDC", decimals: 6, symbol: "USDC" };
    const o = mapEngineOpportunity(
      sampleOpp({ numeraire: usdc, chain_ids: [99999] }),
      1_700_000_000_000,
    );
    expect(o.network).toBe("chain-99999");
  });

  it("builds a route leg per hop with derived prices", () => {
    const o = mapEngineOpportunity(sampleOpp(), 1_700_000_000_000);
    expect(o.route).toHaveLength(2);
    expect(o.route[0]!.tokenIn).toBe("USDC");
    expect(o.route[0]!.tokenOut).toBe("WETH");
    // 0.03125 WETH out per 100 USDC in
    expect(o.route[0]!.price).toBeCloseTo(0.03125 / 100, 12);
    expect(o.route[1]!.tokenIn).toBe("WETH");
  });

  it("sets a stable, block-independent id (same route updates in place)", () => {
    const a = opportunityId(sampleOpp({ block: { chain_id: 8453, number: 1, hash: "0x1", timestamp: 1 } }));
    const b = opportunityId(sampleOpp({ block: { chain_id: 8453, number: 2, hash: "0x2", timestamp: 2 } }));
    expect(a).toBe(b);
    expect(a).toContain("l2arb:two_hop:8453:");
  });

  it("throws on structurally invalid input (no legs)", () => {
    expect(() => mapEngineOpportunity(sampleOpp({ legs: [] }), 0)).toThrow();
  });

  it("sets expiry ttl relative to receipt time", () => {
    const now = 1_700_000_000_000;
    const o = mapEngineOpportunity(sampleOpp(), now, 5000);
    expect(o.ts).toBe(now);
    expect(o.expiresAt).toBe(now + 5000);
    expect(o.status).toBe("new");
  });
});

describe("isUsdStable", () => {
  it("recognizes common stablecoins case-insensitively", () => {
    expect(isUsdStable("USDC")).toBe(true);
    expect(isUsdStable("usdt")).toBe(true);
    expect(isUsdStable("DAI")).toBe(true);
    expect(isUsdStable("WETH")).toBe(false);
    expect(isUsdStable("ARB")).toBe(false);
  });
});
