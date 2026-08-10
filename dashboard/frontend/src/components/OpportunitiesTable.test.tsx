import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { Row } from "./OpportunitiesTable";
import type { ArbitrageOpportunity } from "../lib/types";

function opp(over: Partial<ArbitrageOpportunity> = {}): ArbitrageOpportunity {
  return {
    id: "opp-1",
    ts: Date.now(),
    network: "base",
    chainId: 8453,
    tokenIn: "USDC",
    route: [
      { dex: "uniswap-v3", tokenIn: "USDC", tokenOut: "WETH", price: 1, poolFeeBps: 5 },
      { dex: "aerodrome", tokenIn: "WETH", tokenOut: "USDC", price: 1, poolFeeBps: 5 },
    ],
    amountInUsd: 1000,
    grossProfitUsd: 10,
    flashLoanFeeUsd: 0,
    gasCostUsd: 1,
    netProfitUsd: 9,
    profitBps: 90,
    spreadBps: 90,
    confidence: 0.8,
    status: "new",
    expiresAt: Date.now() + 10_000,
    isCrossChain: false,
    ...over,
  };
}

function renderRow(o: ArbitrageOpportunity, extra: Partial<{ paper: boolean; executing: boolean }> = {}) {
  return render(
    <table>
      <tbody>
        <Row
          o={o}
          now={Date.now()}
          executing={extra.executing ?? false}
          paper={extra.paper ?? true}
          onExecute={vi.fn()}
        />
      </tbody>
    </table>,
  );
}

// Regression coverage for the cross-chain honesty gap found in the 7th audit
// pass (2026-08-10): a cross-chain opportunity was rendered identically to a
// same-chain one — no destination chain, no settlement-time signal, and an
// "Execute" tooltip that implied a same-chain simulated/broadcast fill even
// though PaperExecutor always records it "skipped" (D2) and LiveExecutor
// always refuses (uniformly). See root CLAUDE.md invariant 7 ("verified
// honesty") and the class of bug this repo treats as highest priority.
describe("OpportunitiesTable Row — cross-chain honesty", () => {
  it("shows no destination/settle marker for an ordinary same-chain row", () => {
    renderRow(opp());
    expect(screen.queryByText(/destination unresolved/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/settle/i)).not.toBeInTheDocument();
    const btn = screen.getByRole("button", { name: /execute/i });
    expect(btn).toHaveAttribute("title", "Simulated fill (paper mode)");
  });

  it("renders the resolved destination network + settle time for a cross-chain row", () => {
    renderRow(opp({ isCrossChain: true, destNetwork: "arbitrum", settleSeconds: 600 }));
    expect(screen.getByText("arbitrum")).toBeInTheDocument();
    expect(screen.getByText(/~600s settle/)).toBeInTheDocument();
  });

  it("honestly says 'destination unresolved' rather than guessing when destNetwork is absent", () => {
    renderRow(opp({ isCrossChain: true, destNetwork: undefined }));
    expect(screen.getByText(/destination unresolved/i)).toBeInTheDocument();
  });

  it("gives the Execute button a cross-chain-specific tooltip in paper mode, not the same-chain text", () => {
    renderRow(opp({ isCrossChain: true, destNetwork: "arbitrum" }), { paper: true });
    const btn = screen.getByRole("button", { name: /execute/i });
    expect(btn.getAttribute("title")).toMatch(/non-atomic two-leg/i);
    expect(btn.getAttribute("title")).not.toMatch(/simulated fill/i);
  });

  it("gives the Execute button the same cross-chain tooltip in live mode (LiveExecutor refuses uniformly)", () => {
    renderRow(opp({ isCrossChain: true, destNetwork: "arbitrum" }), { paper: false });
    const btn = screen.getByRole("button", { name: /execute/i });
    expect(btn.getAttribute("title")).toMatch(/non-atomic two-leg/i);
    expect(btn.getAttribute("title")).not.toMatch(/broadcast live transaction/i);
  });
});
