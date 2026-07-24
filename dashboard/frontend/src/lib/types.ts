/** Types mirrored from the backend API contract (see backend/openapi.yaml). */

export interface Settings {
  engineEnabled: boolean;
  executionMode: "paper" | "live";
  autoExecute: boolean;
  networks: string[];
  baseToken: string;
  tokens: string[];
  dexes: string[];
  flashLoanProvider: "aave-v3" | "balancer-v2" | "uniswap-v3";
  loanAmountUsd: number;
  minProfitUsd: number;
  minProfitBps: number;
  slippageBps: number;
  maxGasGwei: number;
  priorityFeeGwei: number;
  gasLimit: number;
  scanIntervalMs: number;
  maxConcurrentTrades: number;
  cooldownMs: number;
  deadlineSec: number;
  maxDailyLossUsd: number;
  maxPositionUsd: number;
}

export interface RouteLeg {
  dex: string;
  tokenIn: string;
  tokenOut: string;
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

export interface ArbitrageOpportunity {
  id: string;
  ts: number;
  network: string;
  chainId: number;
  tokenIn: string;
  route: RouteLeg[];
  amountInUsd: number;
  grossProfitUsd: number;
  flashLoanFeeUsd: number;
  gasCostUsd: number;
  netProfitUsd: number;
  profitBps: number;
  spreadBps: number;
  confidence: number;
  status: OpportunityStatus;
  expiresAt: number;
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

export interface DexInfo {
  key: string;
  name: string;
  feeBps: number;
}

export interface NetworkInfo {
  key: string;
  name: string;
  chainId: number;
  nativeCurrency: string;
  explorer: string;
  typicalGasUsd: number;
  dexes: DexInfo[];
  enabledByDefault: boolean;
}

export interface Snapshot {
  settings: Settings;
  networks: NetworkInfo[];
  opportunities: ArbitrageOpportunity[];
  stats: EngineStats;
}

/** Envelope for every WebSocket message. */
export interface WsEnvelope<T = unknown> {
  type:
    | "snapshot"
    | "opportunity"
    | "opportunity:remove"
    | "execution"
    | "stats"
    | "settings"
    | "alert"
    | "pong";
  payload: T;
  ts: number;
}
