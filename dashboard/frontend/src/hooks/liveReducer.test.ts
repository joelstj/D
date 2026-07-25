import { describe, it, expect } from "vitest";
import { liveReducer, initialLiveState, MAX_OPPS, MAX_CLIENT_LATENCY } from "./liveReducer";
import type { ArbitrageOpportunity, EngineStats, LatencySnapshot, Snapshot } from "../lib/types";

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

  it("stores the latency snapshot from a `latency` message and from the snapshot", () => {
    const snapshot: LatencySnapshot = {
      components: [{ component: "engine", stages: [] }],
      endToEnd: { stage: "end_to_end", last: 40, avg: 42, p50: 41, p95: 55, p99: 60, count: 9 },
      samples: 9,
      anchored: true,
      updatedAt: 123,
    };
    const viaMsg = liveReducer(initialLiveState, { type: "latency", snapshot });
    expect(viaMsg.latency?.endToEnd?.p50).toBe(41);

    const viaSnap = liveReducer(initialLiveState, {
      type: "snapshot",
      snapshot: { settings: null as never, networks: [], opportunities: [], stats, latency: snapshot },
    });
    expect(viaSnap.latency?.samples).toBe(9);
  });

  it("accumulates client-side end-to-end samples and ignores bad values", () => {
    let s = liveReducer(initialLiveState, { type: "client-latency", ms: 12 });
    s = liveReducer(s, { type: "client-latency", ms: 18 });
    s = liveReducer(s, { type: "client-latency", ms: -1 }); // ignored
    s = liveReducer(s, { type: "client-latency", ms: NaN }); // ignored
    expect(s.clientLatencyMs).toEqual([12, 18]);
  });

  it("bounds the client latency window to MAX_CLIENT_LATENCY", () => {
    let s = initialLiveState;
    for (let i = 0; i < MAX_CLIENT_LATENCY + 10; i++) {
      s = liveReducer(s, { type: "client-latency", ms: i });
    }
    expect(s.clientLatencyMs).toHaveLength(MAX_CLIENT_LATENCY);
    expect(s.clientLatencyMs[s.clientLatencyMs.length - 1]).toBe(MAX_CLIENT_LATENCY + 9);
  });
});
