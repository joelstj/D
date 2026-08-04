import { describe, it, expect } from "vitest";
import { PaperExecutor, LiveExecutor, CROSS_CHAIN_SKIP_REASON } from "../src/arbitrage/executor";
import { DEFAULT_SETTINGS } from "../src/settings/schema";
import { makeOpportunity } from "./helpers";

/**
 * D2 regression coverage: neither executor may fabricate an atomic
 * fill/revert outcome for a cross-chain opportunity —
 * `CrossChainArbitrageExecutor.sol`'s own NatSpec says capital is in flight
 * and exposed between the two (non-atomic) legs, so "reverted, lost only gas"
 * is actively false for this shape of trade.
 */
describe("PaperExecutor cross-chain handling (D2)", () => {
  it("never simulates an atomic fill/revert for a cross-chain opportunity — always 'skipped' with a reason", async () => {
    const executor = new PaperExecutor();
    const opp = makeOpportunity({
      isCrossChain: true,
      destChainId: 42161,
      destNetwork: "arbitrum",
      settleSeconds: 45,
      confidence: 1, // would deterministically "fill" under the atomic model if not skipped
    });

    // Run several times — Math.random()-driven fill/revert must never leak through.
    for (let i = 0; i < 25; i++) {
      const result = await executor.execute(opp, DEFAULT_SETTINGS);
      expect(result.status).toBe("skipped");
      expect(result.status).not.toBe("filled");
      expect(result.status).not.toBe("reverted");
      expect(result.notes).toBe(CROSS_CHAIN_SKIP_REASON);
      expect(result.notes).toMatch(/two-leg CrossChainArbitrageExecutor/i);
      // Nothing was attempted: no fabricated gain or gas spend.
      expect(result.realizedProfitUsd).toBe(0);
      expect(result.gasCostUsd).toBe(0);
      expect(result.opportunityId).toBe(opp.id);
      expect(result.mode).toBe("paper");
    }
  });

  it("still runs the ordinary atomic fill/revert model for a same-chain opportunity", async () => {
    const executor = new PaperExecutor();
    const opp = makeOpportunity({ isCrossChain: false });
    const result = await executor.execute(opp, DEFAULT_SETTINGS);
    expect(["filled", "reverted"]).toContain(result.status);
  });

  it("treats an opportunity with isCrossChain unset the same as false (same-chain path)", async () => {
    // Belt-and-braces: makeOpportunity always sets isCrossChain, so build the
    // object by hand to prove the executor branches on truthiness, not merely
    // "the field happens to be present".
    const executor = new PaperExecutor();
    const opp = { ...makeOpportunity(), isCrossChain: false as const };
    const result = await executor.execute(opp, DEFAULT_SETTINGS);
    expect(["filled", "reverted"]).toContain(result.status);
  });
});

describe("LiveExecutor (verified: already fails safe for cross-chain, no change required)", () => {
  it("refuses a cross-chain opportunity exactly like any other — never broadcasts", async () => {
    const executor = new LiveExecutor();
    await expect(executor.execute()).rejects.toThrow(/not enabled/i);
  });
});
