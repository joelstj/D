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
  /**
   * Whether the `…Usd` fields are genuine dollars (numeraire is a USD stablecoin).
   * When `false`, the magnitudes are in numeraire base units — render with the
   * token symbol, never a `$`. Absent ⇒ treated as USD.
   */
  numeraireIsUsd?: boolean;
  profitBps: number;
  spreadBps: number;
  confidence: number;
  status: OpportunityStatus;
  expiresAt: number;
  /** Ingestion tick-start wall clock (ms) for end-to-end latency; real feed only. */
  originWallMs?: number;
  /**
   * Whether this opportunity spans two chains (a source/buy chain and a
   * different destination/sell chain). Always present — `false` for an
   * ordinary same-chain opportunity. Cross-chain arbitrage is non-atomic:
   * it executes as two separate transactions with capital exposed in flight
   * between them.
   */
  isCrossChain: boolean;
  /** Destination (sell) chain id for a cross-chain opportunity. Present only
   *  when `isCrossChain` is true and the destination was unambiguous. */
  destChainId?: number;
  /** Dashboard network key for {@link destChainId} (e.g. `"arbitrum"`).
   *  Present under the same conditions as `destChainId`. */
  destNetwork?: string;
  /** Expected wall-clock seconds between the source leg settling and the
   *  destination leg becoming executable (bridge transit time). Present only
   *  when `isCrossChain` is true. */
  settleSeconds?: number;
}

/** Rolling statistics for one measured latency stage. */
export interface StageStat {
  stage: string;
  last: number;
  avg: number;
  p50: number;
  p95: number;
  p99: number;
  count: number;
}

/** A component (ingestion / engine / dashboard) rolled up to its stages. */
export interface ComponentLatency {
  component: string;
  stages: StageStat[];
}

/** The end-to-end pipeline latency snapshot from `GET /api/latency` / `/ws`. */
export interface LatencySnapshot {
  components: ComponentLatency[];
  endToEnd: StageStat | null;
  samples: number;
  anchored: boolean;
  updatedAt: number;
}

/** One read-only on-chain execution-readiness measurement. */
export interface ExecutionLatencySample {
  configured: boolean;
  healthy: boolean;
  chain: string | null;
  blockNumber: number | null;
  gasPriceGwei: number | null;
  stages: { stage: string; ms: number }[];
  contractProbed: boolean;
  error: string | null;
  checkedAt: number;
}

export interface ExecutionResult {
  id: string;
  opportunityId: string;
  ts: number;
  mode: "paper" | "live";
  /** `"skipped"` means no fill/revert was attempted or modelled — used for a
   *  cross-chain opportunity, never simulated as an atomic fill/revert. */
  status: "filled" | "reverted" | "skipped";
  network: string;
  requestedProfitUsd: number;
  realizedProfitUsd: number;
  gasCostUsd: number;
  /** Whether the `…Usd` figures are genuine dollars (numeraire is a USD stable). */
  numeraireIsUsd?: boolean;
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
  latency?: LatencySnapshot;
}

/* ------------------------------------------------------------ Contracts --- */

export type ContractAction = "verify-provider" | "compile" | "deploy" | "ready";

export interface DeploymentRecord {
  network: string;
  chainId: number;
  address: string;
  crossChainAddress: string | null;
  deployer?: string;
  txHash?: string;
  deployedAt: string;
}

export interface CompileStatus {
  name: string;
  role: "atomic" | "crosschain";
  compiled: boolean;
  bytecodeHash: string | null;
  bytecodeSize: number | null;
}

export interface NetworkContractStatus {
  key: string;
  name: string;
  chainId: number;
  explorer: string;
  providerVerified: boolean;
  aavePool: string | null;
  balancerVault: string | null;
  deployment: DeploymentRecord | null;
  envWired: boolean;
  action: ContractAction;
}

export interface ContractsStatus {
  available: boolean;
  contractsDir: string;
  compiled: boolean;
  artifacts: CompileStatus[];
  networks: NetworkContractStatus[];
  generatedAt: number;
}

export interface ContractArtifact {
  contractName: string;
  abi: unknown[];
  bytecode: `0x${string}`;
}

export interface DeployParams {
  network: string;
  chainId: number;
  contract: string;
  providerVerified: boolean;
  aavePool: string;
  balancerVault: string;
  args: string[];
}

export interface ReadinessResult {
  network: string;
  chainId: number;
  address: string | null;
  crossChainAddress: string | null;
  configured: boolean;
  hasCode: boolean;
  premiumBps: number | null;
  crossChainHasCode: boolean | null;
  /** Live `paused()` kill-switch state for the atomic executor. `null` when
   *  unreadable (not deployed, no probe, or the view reverted) — never
   *  defaulted to `false`, since "not paused" is a safety claim that must be
   *  genuinely read (root `CLAUDE.md` §12 finding O2). */
  paused: boolean | null;
  /** Same as {@link paused}, for the cross-chain executor. */
  crossChainPaused: boolean | null;
  healthy: boolean;
  error: string | null;
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
    | "latency"
    | "pong";
  payload: T;
  ts: number;
}
