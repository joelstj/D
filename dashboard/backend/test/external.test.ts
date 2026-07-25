import { describe, it, expect, beforeEach, vi } from "vitest";
import { ExternalProvider, type WebSocketLike } from "../src/arbitrage/providers/external";
import { LatencyMonitor } from "../src/arbitrage/latency";
import { DEFAULT_SETTINGS } from "../src/settings/schema";
import type { EngineOpportunity } from "../src/arbitrage/providers/engineMap";

/** Deterministic in-memory WebSocket double (no sockets, no ports, no timing). */
class FakeWs implements WebSocketLike {
  static instances: FakeWs[] = [];
  handlers = new Map<string, (...a: unknown[]) => void>();
  closed = false;
  constructor(public url: string) {
    FakeWs.instances.push(this);
  }
  on(event: string, cb: (...a: unknown[]) => void) {
    this.handlers.set(event, cb);
  }
  close() {
    this.closed = true;
  }
  emit(event: string, ...args: unknown[]) {
    this.handlers.get(event)?.(...args);
  }
}

function opp(): EngineOpportunity {
  const usdc = { chain_id: 8453, address: "0xUSDC", decimals: 6, symbol: "USDC" };
  const weth = { chain_id: 8453, address: "0xWETH", decimals: 18, symbol: "WETH" };
  return {
    strategy: "two_hop",
    numeraire: usdc,
    input_amount: "100000000",
    output_amount: "100500000",
    gross_profit: "500000",
    gas_cost: "120000",
    bridge_cost: "0",
    net_profit: "380000",
    profit_bps: 38,
    expected_net: "360000",
    score: 0.9,
    hops: 2,
    chain_ids: [8453],
    is_cross_chain: false,
    settle_seconds: 0,
    verified: true,
    block: { chain_id: 8453, number: 1, hash: "0x1", timestamp: 1 },
    risk: { success_probability: 0.85, capture_ratio: 0.8, frontrun_risk: 0.1, notes: [] },
    legs: [
      { pool: "0xPool1000000000000000000000000000000000001", token_in: usdc, token_out: weth, amount_in: "100000000", amount_out: "31250000000000000" },
      { pool: "0xPool2000000000000000000000000000000000002", token_in: weth, token_out: usdc, amount_in: "31250000000000000", amount_out: "100500000" },
    ],
  };
}

function envelope(opps: unknown[], kind = "opportunities") {
  return JSON.stringify({ schema_version: 1, kind, chain_blocks: { "8453": 1 }, payload: { count: opps.length, opportunities: opps } });
}

/** An envelope carrying the latency trace (ingestion latency + engine timing). */
function tracedEnvelope(opps: unknown[], originWallMs: number) {
  return JSON.stringify({
    schema_version: 1,
    kind: "opportunities",
    chain_blocks: { "8453": 1 },
    latency: {
      origin_wall_ms: originWallMs,
      component: "ingestion",
      stages: [
        { stage: "build", ms: 0.9 },
        { stage: "engine_roundtrip", ms: 8.5 },
      ],
      total_ms: 9.4,
    },
    payload: {
      count: opps.length,
      opportunities: opps,
      timing: {
        component: "engine",
        stages: [
          { stage: "detect", ms: 3.1 },
          { stage: "rank", ms: 0.2 },
        ],
        total_ms: 3.3,
      },
    },
  });
}

function newProvider(opts = {}) {
  return new ExternalProvider("ws://ingestion:9001", {
    WebSocketCtor: FakeWs as unknown as new (url: string) => WebSocketLike,
    ...opts,
  });
}

describe("ExternalProvider", () => {
  beforeEach(() => {
    FakeWs.instances = [];
  });

  it("maps opportunity frames and drains them on scan", async () => {
    const p = newProvider();
    p.start();
    const ws = FakeWs.instances[0]!;
    ws.emit("open");
    ws.emit("message", envelope([opp()]));

    const batch = await p.scan(DEFAULT_SETTINGS);
    expect(batch).toHaveLength(1);
    expect(batch[0]!.network).toBe("base");
    expect(batch[0]!.netProfitUsd).toBeCloseTo(0.38, 9);

    // Drained: a second scan with no new frames returns nothing.
    expect(await p.scan(DEFAULT_SETTINGS)).toHaveLength(0);
    p.stop();
  });

  it("upserts by stable id instead of duplicating rows", async () => {
    const p = newProvider();
    p.start();
    const ws = FakeWs.instances[0]!;
    ws.emit("message", envelope([opp()]));
    ws.emit("message", envelope([opp()])); // same route, later block
    const batch = await p.scan(DEFAULT_SETTINGS);
    expect(batch).toHaveLength(1);
    expect(p.getState().oppsMapped).toBe(2); // received twice, one buffered row
    p.stop();
  });

  it("ignores non-opportunity frames (e.g. snapshots)", async () => {
    const p = newProvider();
    p.start();
    const ws = FakeWs.instances[0]!;
    ws.emit("message", envelope([opp()], "snapshot"));
    expect(await p.scan(DEFAULT_SETTINGS)).toHaveLength(0);
    p.stop();
  });

  it("drops malformed opportunities without crashing", async () => {
    const p = newProvider();
    p.start();
    const ws = FakeWs.instances[0]!;
    ws.emit("message", envelope([{ strategy: "bad", numeraire: null, legs: [] }]));
    expect(await p.scan(DEFAULT_SETTINGS)).toHaveLength(0);
    expect(p.getState().oppsDropped).toBe(1);
    p.stop();
  });

  it("tolerates unparseable frames", async () => {
    const p = newProvider();
    p.start();
    const ws = FakeWs.instances[0]!;
    expect(() => ws.emit("message", "not json{")).not.toThrow();
    expect(await p.scan(DEFAULT_SETTINGS)).toHaveLength(0);
    p.stop();
  });

  it("accepts a Buffer frame", async () => {
    const p = newProvider();
    p.start();
    const ws = FakeWs.instances[0]!;
    ws.emit("message", Buffer.from(envelope([opp()]), "utf8"));
    expect(await p.scan(DEFAULT_SETTINGS)).toHaveLength(1);
    p.stop();
  });

  it("prunes opportunities past their ttl", async () => {
    const p = newProvider({ ttlMs: 0 });
    p.start();
    const ws = FakeWs.instances[0]!;
    ws.emit("message", envelope([opp()]));
    // ttl=0 → expiresAt == receipt time, so a later scan prunes it.
    await new Promise((r) => setTimeout(r, 2));
    expect(await p.scan(DEFAULT_SETTINGS)).toHaveLength(0);
    expect(p.getState().buffered).toBe(0);
    p.stop();
  });

  it("feeds the latency monitor the ingestion + engine traces and stamps the anchor", async () => {
    const monitor = new LatencyMonitor();
    const p = newProvider({ latency: monitor });
    p.start();
    const ws = FakeWs.instances[0]!;
    const origin = Date.now() - 5; // 5ms ago, so ingest_to_dashboard is small & positive
    ws.emit("message", tracedEnvelope([opp()], origin));

    const snap = monitor.snapshot();
    // Ingestion + engine stages were relayed into the aggregator...
    const ingestion = snap.components.find((c) => c.component === "ingestion")!;
    expect(ingestion.stages.map((s) => s.stage)).toEqual(["build", "engine_roundtrip"]);
    const engine = snap.components.find((c) => c.component === "engine")!;
    expect(engine.stages.map((s) => s.stage)).toEqual(["detect", "rank"]);
    // ...the dashboard measured its own parse + map...
    const dash = snap.components.find((c) => c.component === "dashboard")!;
    expect(dash.stages.map((s) => s.stage)).toEqual(["parse", "map"]);
    // ...and the cross-process ingest→receipt gap was recorded under "pipeline".
    const pipeline = snap.components.find((c) => c.component === "pipeline")!;
    expect(pipeline.stages.map((s) => s.stage)).toContain("ingest_to_dashboard");

    // The anchor rides on the mapped opportunity for the client-side end-to-end.
    const batch = await p.scan(DEFAULT_SETTINGS);
    expect(batch[0]!.originWallMs).toBe(origin);
    p.stop();
  });

  it("omits the anchor when a frame carries no latency trace", async () => {
    const monitor = new LatencyMonitor();
    const p = newProvider({ latency: monitor });
    p.start();
    const ws = FakeWs.instances[0]!;
    ws.emit("message", envelope([opp()])); // no `latency` field
    const batch = await p.scan(DEFAULT_SETTINGS);
    expect(batch[0]!.originWallMs).toBeUndefined();
    // No ingestion/engine/pipeline components — only the dashboard's own parse/map.
    expect(monitor.snapshot().components.map((c) => c.component)).toEqual(["dashboard"]);
    expect(monitor.snapshot().anchored).toBe(false);
    p.stop();
  });

  it("reconnects after the socket closes", () => {
    vi.useFakeTimers();
    const p = newProvider({ reconnectMs: 1000 });
    p.start();
    expect(FakeWs.instances).toHaveLength(1);
    FakeWs.instances[0]!.emit("close");
    expect(p.getState().connected).toBe(false);
    vi.advanceTimersByTime(1000);
    expect(FakeWs.instances).toHaveLength(2); // a fresh connection was opened
    p.stop();
    vi.useRealTimers();
  });
});
