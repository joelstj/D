import { describe, it, expect } from "vitest";
import { liveReducer, initialLiveState, MAX_OPPS } from "./liveReducer";
import type { ArbitrageOpportunity, EngineStats, Snapshot } from "../lib/types";

function opp(id: string, netProfitUsd = 100): ArbitrageOpportunity {
  return {
    id,
    ts: Date.now(),
    network: "base",
    chainId: 8453,
    tokenIn: "USDC",
    route: [],
    amountInUsd: 50000,
    grossProfitUsd: 150,
    flashLoanFeeUsd: 25,
    gasCostUsd: 0.05,
    netProfitUsd,
    profitBps: 20,
    spreadBps: 30,
    confidence: 0.8,
    status: "new",
    expiresAt: Date.now() + 10000,
  };
}

const stats: EngineStats = {
  running: true,
  dataSource: "simulated",
  executionMode: "paper",
  scans: 3,
  opportunitiesDetected: 5,
  opportunitiesActive: 2,
  executed: 1,
  filled: 1,
  reverted: 0,
  realizedPnlUsd: 42,
  dailyPnlUsd: 42,
  bestNetProfitUsd: 175,
  lastScanTs: 1234,
  uptimeMs: 9999,
};

describe("liveReducer", () => {
  it("hydrates from a snapshot", () => {
    const snapshot: Snapshot = {
      settings: null as never,
      networks: [],
      opportunities: [opp("a"), opp("b")],
      stats,
    };
    const s = liveReducer(initialLiveState, { type: "snapshot", snapshot });
    expect(s.opportunities).toHaveLength(2);
    expect(s.stats?.scans).toBe(3);
  });

  it("prepends new opportunities and dedupes by id", () => {
    let s = liveReducer(initialLiveState, { type: "opportunity", opp: opp("a") });
    s = liveReducer(s, { type: "opportunity", opp: opp("b") });
    s = liveReducer(s, { type: "opportunity", opp: opp("a", 999) }); // same id, updated
    expect(s.opportunities).toHaveLength(2);
    expect(s.opportunities[0]!.id).toBe("a");
    expect(s.opportunities[0]!.netProfitUsd).toBe(999);
  });

  it("caps the opportunity list", () => {
    let s = initialLiveState;
    for (let i = 0; i < MAX_OPPS + 20; i++) {
      s = liveReducer(s, { type: "opportunity", opp: opp(`id-${i}`) });
    }
    expect(s.opportunities).toHaveLength(MAX_OPPS);
  });

  it("removes opportunities by id", () => {
    let s = liveReducer(initialLiveState, { type: "opportunity", opp: opp("a") });
    s = liveReducer(s, { type: "opportunity:remove", id: "a" });
    expect(s.opportunities).toHaveLength(0);
  });

  it("records stats history points", () => {
    let s = liveReducer(initialLiveState, { type: "stats", stats });
    s = liveReducer(s, { type: "stats", stats: { ...stats, opportunitiesActive: 4 } });
    expect(s.history).toHaveLength(2);
    expect(s.history[1]!.active).toBe(4);
  });

  it("stores alerts newest-first", () => {
    let s = liveReducer(initialLiveState, {
      type: "alert",
      alert: { level: "warn", message: "first", ts: 1 },
    });
    s = liveReducer(s, { type: "alert", alert: { level: "error", message: "second", ts: 2 } });
    expect(s.alerts[0]!.message).toBe("second");
  });
});
