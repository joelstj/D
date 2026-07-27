import { randomUUID } from "node:crypto";
import type { Settings } from "../settings/schema";
import type { ArbitrageOpportunity, ExecutionResult } from "./types";

export interface Executor {
  readonly mode: "paper" | "live";
  execute(opp: ArbitrageOpportunity, settings: Settings): Promise<ExecutionResult>;
}

/**
 * Simulates execution without ever broadcasting a transaction. Realized profit
 * is modelled from the opportunity's confidence and configured slippage, so the
 * PnL curve behaves like a real (imperfect) fill stream. This is the default
 * and safe executor — no funds can move.
 */
export class PaperExecutor implements Executor {
  readonly mode = "paper" as const;

  async execute(opp: ArbitrageOpportunity, settings: Settings): Promise<ExecutionResult> {
    // Model network + inclusion latency.
    await delay(60 + Math.random() * 220);

    const filled = Math.random() < opp.confidence;
    const slipFactor = 1 - (Math.random() * settings.slippageBps) / 10_000;
    const realizedProfitUsd = filled
      ? opp.netProfitUsd * slipFactor
      : -opp.gasCostUsd; // reverted: lose gas only (flash loan unwinds atomically)

    return {
      id: randomUUID(),
      opportunityId: opp.id,
      ts: Date.now(),
      mode: this.mode,
      status: filled ? "filled" : "reverted",
      network: opp.network,
      requestedProfitUsd: opp.netProfitUsd,
      realizedProfitUsd,
      gasCostUsd: opp.gasCostUsd,
      numeraireIsUsd: opp.numeraireIsUsd,
      notes: filled ? "paper fill" : "paper revert (edge gone before inclusion)",
    };
  }
}

/**
 * Placeholder for real execution. Building & broadcasting a real flash-loan
 * bundle requires an audited on-chain contract, a funded signer, and MEV
 * protection — all of which are gated behind explicit configuration. Until that
 * is wired, live execution refuses to run rather than doing something unsafe.
 */
export class LiveExecutor implements Executor {
  readonly mode = "live" as const;

  async execute(): Promise<ExecutionResult> {
    throw new Error(
      "Live execution is not enabled in this build. It requires a deployed, audited " +
        "flash-loan contract, a funded signer, and MEV protection. Keep EXECUTION_MODE=paper " +
        "until that integration is complete.",
    );
  }
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
