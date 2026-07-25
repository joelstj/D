import { describe, it, expect } from "vitest";
import { LatencyMonitor, type ComponentTiming } from "../src/arbitrage/latency";

describe("LatencyMonitor", () => {
  it("aggregates stage samples into last/avg/percentiles", () => {
    const m = new LatencyMonitor(240, () => 1000);
    for (const ms of [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]) m.record("dashboard", "scan", ms);
    const snap = m.snapshot();
    expect(snap.updatedAt).toBe(1000);
    const scan = snap.components.find((c) => c.component === "dashboard")!.stages[0]!;
    expect(scan.stage).toBe("scan");
    expect(scan.count).toBe(10);
    expect(scan.last).toBe(10);
    expect(scan.avg).toBe(5.5);
    expect(scan.p50).toBe(5); // nearest-rank: ceil(0.5*10)-1 = index 4 → 5
    expect(scan.p95).toBe(10);
    expect(scan.p99).toBe(10);
  });

  it("ignores non-finite and negative samples (clock hiccups never poison stats)", () => {
    const m = new LatencyMonitor();
    m.record("dashboard", "map", 5);
    m.record("dashboard", "map", -3); // dropped
    m.record("dashboard", "map", NaN); // dropped
    m.record("dashboard", "map", Infinity); // dropped
    const stage = m.snapshot().components[0]!.stages[0]!;
    expect(stage.count).toBe(1);
    expect(stage.last).toBe(5);
  });

  it("records a relayed component timing block (ingestion / engine)", () => {
    const m = new LatencyMonitor();
    const engine: ComponentTiming = {
      component: "engine",
      stages: [
        { stage: "build", ms: 0.5 },
        { stage: "detect", ms: 3.1 },
        { stage: "rank", ms: 0.2 },
        { stage: "serialize", ms: 0.1 },
      ],
      total_ms: 3.9,
    };
    m.recordComponent(engine);
    const comp = m.snapshot().components.find((c) => c.component === "engine")!;
    expect(comp.stages.map((s) => s.stage)).toEqual(["build", "detect", "rank", "serialize"]);
    expect(comp.stages.find((s) => s.stage === "detect")!.last).toBe(3.1);
  });

  it("recordComponent tolerates malformed input", () => {
    const m = new LatencyMonitor();
    m.recordComponent(null);
    m.recordComponent(undefined);
    m.recordComponent({ component: "x", stages: "nope" } as unknown as ComponentTiming);
    expect(m.snapshot().components).toEqual([]);
  });

  it("tracks end-to-end separately from stages and marks anchored", () => {
    const m = new LatencyMonitor();
    expect(m.snapshot().anchored).toBe(false);
    expect(m.snapshot().endToEnd).toBeNull();

    m.recordEndToEnd(42);
    m.recordEndToEnd(58);
    const snap = m.snapshot();
    expect(snap.anchored).toBe(true);
    expect(snap.samples).toBe(2);
    expect(snap.endToEnd).not.toBeNull();
    expect(snap.endToEnd!.avg).toBe(50);
    // The pipeline end_to_end stage is NOT duplicated into the components list.
    expect(snap.components.find((c) => c.component === "pipeline")).toBeUndefined();
  });

  it("preserves component and stage insertion order", () => {
    const m = new LatencyMonitor();
    m.record("ingestion", "build", 1);
    m.record("engine", "detect", 1);
    m.record("dashboard", "scan", 1);
    m.record("dashboard", "fanout", 1);
    const snap = m.snapshot();
    expect(snap.components.map((c) => c.component)).toEqual(["ingestion", "engine", "dashboard"]);
    expect(snap.components[2]!.stages.map((s) => s.stage)).toEqual(["scan", "fanout"]);
  });

  it("bounds each window to windowSize (rolling)", () => {
    const m = new LatencyMonitor(3);
    for (const ms of [1, 2, 3, 4, 5]) m.record("dashboard", "scan", ms);
    const stage = m.snapshot().components[0]!.stages[0]!;
    expect(stage.count).toBe(3); // only the last 3 kept: [3,4,5]
    expect(stage.last).toBe(5);
    expect(stage.avg).toBe(4);
  });
});
