import { describe, it, expect, beforeEach, vi } from "vitest";
import { ExternalProvider, type WebSocketLike } from "../src/arbitrage/providers/external";
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
