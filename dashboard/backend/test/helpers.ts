import { randomUUID } from "node:crypto";
import type { OpportunityProvider } from "../src/arbitrage/providers/provider";
import type { ArbitrageOpportunity } from "../src/arbitrage/types";
import type { Settings } from "../src/settings/schema";
import { PaperExecutor, LiveExecutor } from "../src/arbitrage/executor";

/** Standard executor pair for tests (paper + gated live). */
export function pairExecutors() {
  return { paper: new PaperExecutor(), live: new LiveExecutor() };
}

export function makeOpportunity(over: Partial<ArbitrageOpportunity> = {}): ArbitrageOpportunity {
  const now = Date.now();
  return {
    id: randomUUID(),
    ts: now,
    network: "base",
    chainId: 8453,
    tokenIn: "USDC",
    route: [
      { dex: "uniswap-v3", tokenIn: "USDC", tokenOut: "WETH", price: 1 / 3200, poolFeeBps: 5 },
      { dex: "aerodrome", tokenIn: "WETH", tokenOut: "USDC", price: 3210, poolFeeBps: 5 },
    ],
    amountInUsd: 50_000,
    grossProfitUsd: 200,
    flashLoanFeeUsd: 25,
    gasCostUsd: 0.05,
    netProfitUsd: 175,
    profitBps: 35,
    spreadBps: 40,
    confidence: 0.9,
    status: "new",
    expiresAt: now + 10_000,
    ...over,
  };
}

/** Provider that returns a fixed set of opportunities each scan. */
export class StubProvider implements OpportunityProvider {
  readonly kind = "simulated" as const;
  constructor(private batch: ArbitrageOpportunity[] = []) {}
  start() {}
  stop() {}
  setBatch(batch: ArbitrageOpportunity[]) {
    this.batch = batch;
  }
  async scan(_settings: Settings): Promise<ArbitrageOpportunity[]> {
    // Return fresh clones so each scan produces distinct ids/timestamps, but
    // keep the (future) expiry so opportunities survive until explicitly expired.
    const now = Date.now();
    return this.batch.map((o) => ({ ...o, id: randomUUID(), ts: now }));
  }
}
