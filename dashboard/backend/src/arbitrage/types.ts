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
  /**
   * Whether the `…Usd` fields above are genuine US dollars. They are dollars only
   * when the numeraire (the borrowed/quote token, see {@link tokenIn}) is a USD
   * stablecoin. When `false`, those magnitudes are in **numeraire base units**,
   * not dollars — no price is fabricated to convert them — and the UI must render
   * them with the token symbol, never a `$`. Absent ⇒ treated as USD (the
   * simulated/live providers are USD-denominated by construction).
   */
  numeraireIsUsd?: boolean;
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
  /**
   * Whether this opportunity spans two chains (a source/buy chain and a
   * different destination/sell chain). Always present — `false` for an
   * ordinary same-chain opportunity. Cross-chain arbitrage cannot be
   * atomically flash-loaned (a single transaction cannot span two chains);
   * it executes as two separate, non-atomic transactions with capital
   * exposed in flight between them — see
   * `contracts/contracts/crosschain/CrossChainArbitrageExecutor.sol`'s
   * NatSpec for the authoritative framing.
   */
  isCrossChain: boolean;
  /**
   * Destination (sell) chain id for a cross-chain opportunity. Present only
   * when `isCrossChain` is true AND the source engine payload's `chain_ids`
   * unambiguously identified exactly one chain other than the source
   * {@link chainId} — never guessed at from ambiguous data.
   */
  destChainId?: number;
  /**
   * The dashboard network *key* for {@link destChainId} (e.g. `"arbitrum"`),
   * resolved through the same chainId→key network-registry lookup used for
   * {@link network} — so downstream code (settings filters, UI) never needs
   * to re-implement chainId→key resolution. Present under the same
   * conditions as `destChainId`.
   */
  destNetwork?: string;
  /**
   * Expected wall-clock seconds between the source leg settling and the
   * destination leg becoming executable (bridge transit time). Present only
   * for a cross-chain opportunity (`isCrossChain: true`).
   */
  settleSeconds?: number;
}

export interface ExecutionResult {
  id: string;
  opportunityId: string;
  ts: number;
  mode: "paper" | "live";
  /**
   * `"skipped"` means no fill/revert was attempted or modelled at all — used
   * for a cross-chain opportunity, which `PaperExecutor` refuses to simulate
   * with atomic fill/revert semantics (see {@link PaperExecutor} in
   * `executor.ts`: `CrossChainArbitrageExecutor.sol` is non-atomic, so
   * pretending a "revert" only costs gas would be dishonest).
   */
  status: "filled" | "reverted" | "skipped";
  network: string;
  requestedProfitUsd: number;
  realizedProfitUsd: number;
  gasCostUsd: number;
  /** Whether the `…Usd` figures are genuine dollars (numeraire is a USD stable).
   *  Carried from the executed opportunity; absent ⇒ treated as USD. */
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
