// Type definitions for @l2/sdk

export interface L2ClientOptions {
  baseUrl?: string;
  wsUrl?: string;
  apiKey?: string;
}

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
  status: string;
  expiresAt: number;
}

export interface ExecutionResult {
  id: string;
  opportunityId: string;
  ts: number;
  mode: "paper" | "live";
  status: "filled" | "reverted";
  network: string;
  realizedProfitUsd: number;
  gasCostUsd: number;
  txHash?: string;
  notes?: string;
}

export interface EngineStats {
  running: boolean;
  dataSource: "simulated" | "live";
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

export interface WsMessage {
  type:
    | "snapshot"
    | "opportunity"
    | "opportunity:remove"
    | "execution"
    | "stats"
    | "settings"
    | "alert"
    | "pong";
  payload: unknown;
  ts: number;
}

export interface SubscribeOptions {
  onOpen?: () => void;
  onClose?: () => void;
  reconnect?: boolean;
}

export class L2ArbitrageClient {
  constructor(options?: L2ClientOptions);
  baseUrl: string;
  wsUrl: string;
  apiKey?: string;
  health(): Promise<{ status: string; version: string; dataSource: string; executionMode: string }>;
  networks(): Promise<{ networks: unknown[] }>;
  getSettings(): Promise<Settings>;
  updateSettings(patch: Partial<Settings>): Promise<Settings>;
  resetSettings(): Promise<Settings>;
  opportunities(opts?: { limit?: number; network?: string }): Promise<{
    opportunities: ArbitrageOpportunity[];
    total: number;
  }>;
  stats(): Promise<EngineStats>;
  execute(id: string): Promise<ExecutionResult>;
  setEngineEnabled(enabled: boolean): Promise<Settings>;
  subscribe(onMessage: (msg: WsMessage) => void, opts?: SubscribeOptions): () => void;
}

export default L2ArbitrageClient;
