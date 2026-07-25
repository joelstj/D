/** Ordered swap in an arbitrage route. */
export interface RouteLeg {
  dex: string;
  tokenIn: string;
  tokenOut: string;
  /** tokenOut received per 1 tokenIn on this venue. */
  price: number;
  poolFeeBps: number;
}

export type OpportunityStatus =
  | "new"
  | "executing"
  | "filled"
  | "reverted"
  | "expired"
  | "skipped";

/** A detected (potential) flash-loan arbitrage opportunity. */
export interface ArbitrageOpportunity {
  id: string;
  /** Detection time (epoch ms). */
  ts: number;
  network: string;
  chainId: number;
  /** Asset borrowed via flash loan and repaid at the end. */
  tokenIn: string;
  /** Ordered venues the capital flows through. */
  route: RouteLeg[];
  amountInUsd: number;
  grossProfitUsd: number;
  flashLoanFeeUsd: number;
  gasCostUsd: number;
  netProfitUsd: number;
  /** Net profit relative to loan size, in basis points. */
  profitBps: number;
  /** Raw price spread between the cheap and expensive venue, in bps. */
  spreadBps: number;
  /** Model confidence 0..1 that this fills profitably. */
  confidence: number;
  status: OpportunityStatus;
  expiresAt: number;
  /**
   * Ingestion tick-start wall-clock (ms) this opportunity's batch was anchored at,
   * for end-to-end latency measurement (root `CLAUDE.md` latency-health pipeline).
   * Present only on real (external) detections; single-host wall clock, so the UI
   * measures `Date.now() - originWallMs` for the ingest → displayed latency.
   */
  originWallMs?: number;
}

export interface ExecutionResult {
  id: string;
  opportunityId: string;
  ts: number;
  mode: "paper" | "live";
  status: "filled" | "reverted";
  network: string;
  requestedProfitUsd: number;
  realizedProfitUsd: number;
  gasCostUsd: number;
  txHash?: string;
  notes?: string;
}

export interface EngineStats {
  running: boolean;
  dataSource: "simulated" | "live" | "external";
  executionMode: "paper" | "live";
  scans: number;
  opportunitiesDetected: number;
  opportunitiesActive: number;
  executed: number;
  filled: number;
  reverted: number;
  realizedPnlUsd: number;
  dailyPnlUsd: number;
  bestNetProfitUsd: number;
  lastScanTs: number | null;
  uptimeMs: number;
}
