import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { LatencyPanelView, fmtMs } from "./LatencyPanel";
import type { ExecutionLatencySample, LatencySnapshot, StageStat } from "../lib/types";

function stat(stage: string, p50: number): StageStat {
  return { stage, last: p50, avg: p50, p50, p95: p50 * 1.5, p99: p50 * 2, count: 10 };
}

const snapshot: LatencySnapshot = {
  components: [
    { component: "ingestion", stages: [stat("build", 0.9), stat("engine_roundtrip", 8.5)] },
    { component: "engine", stages: [stat("detect", 3.1), stat("rank", 0.2)] },
    { component: "dashboard", stages: [stat("parse", 0.1), stat("scan", 0.4), stat("fanout", 0.2)] },
    { component: "pipeline", stages: [stat("ingest_to_dashboard", 12)] },
  ],
  endToEnd: { stage: "end_to_end", last: 15, avg: 16, p50: 15, p95: 22, p99: 30, count: 25 },
  samples: 25,
  anchored: true,
  updatedAt: 1000,
};

describe("fmtMs", () => {
  it("uses adaptive precision", () => {
    expect(fmtMs(0.5)).toBe("0.50ms"); // < 10ms → 2 decimals
    expect(fmtMs(8.5)).toBe("8.50ms");
    expect(fmtMs(85)).toBe("85.0ms"); // 10–100ms → 1 decimal
    expect(fmtMs(150)).toBe("150ms"); // ≥ 100ms → whole ms
    expect(fmtMs(2500)).toBe("2.50s"); // ≥ 1s → seconds
    expect(fmtMs(NaN)).toBe("—");
  });
});

describe("LatencyPanelView", () => {
  it("renders the end-to-end headline and every component's stages", () => {
    render(<LatencyPanelView snapshot={snapshot} clientLatencyMs={[14, 16, 15]} execution={null} />);
    expect(screen.getByText("Pipeline Latency")).toBeInTheDocument();
    expect(screen.getByText("live feed")).toBeInTheDocument();
    // Component labels and a representative stage from each.
    expect(screen.getByText("Ingestion · Rust")).toBeInTheDocument();
    expect(screen.getByText("Detection · Python")).toBeInTheDocument();
    expect(screen.getByText("engine_roundtrip")).toBeInTheDocument();
    expect(screen.getByText("detect")).toBeInTheDocument();
    expect(screen.getByText("ingest_to_dashboard")).toBeInTheDocument();
  });

  it("shows an empty-state hint and 'no feed' badge without a snapshot", () => {
    render(<LatencyPanelView snapshot={null} clientLatencyMs={[]} execution={null} />);
    expect(screen.getByText("no feed")).toBeInTheDocument();
    expect(screen.getByText(/only the dashboard's own stages are measurable/i)).toBeInTheDocument();
  });

  it("labels execution readiness as not-configured in paper mode", () => {
    const execution: ExecutionLatencySample = {
      configured: false,
      healthy: false,
      chain: null,
      blockNumber: null,
      gasPriceGwei: null,
      stages: [],
      contractProbed: false,
      error: "no RPC configured",
      checkedAt: 1,
    };
    render(<LatencyPanelView snapshot={snapshot} clientLatencyMs={[]} execution={execution} />);
    expect(screen.getByText("not configured")).toBeInTheDocument();
    expect(screen.getByText(/never broadcasts/i)).toBeInTheDocument();
  });

  it("renders the read-only execution stages when healthy", () => {
    const execution: ExecutionLatencySample = {
      configured: true,
      healthy: true,
      chain: "arbitrum",
      blockNumber: 21_000_000,
      gasPriceGwei: 1.5,
      stages: [
        { stage: "rpc_block", ms: 5 },
        { stage: "rpc_gas", ms: 2 },
      ],
      contractProbed: false,
      error: null,
      checkedAt: 1,
    };
    render(<LatencyPanelView snapshot={snapshot} clientLatencyMs={[]} execution={execution} />);
    expect(screen.getByText("healthy")).toBeInTheDocument();
    expect(screen.getByText("rpc_block")).toBeInTheDocument();
    expect(screen.getByText(/block 21000000/)).toBeInTheDocument();
  });
});
