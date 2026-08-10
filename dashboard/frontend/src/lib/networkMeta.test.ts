import { describe, it, expect } from "vitest";
import { networkColor, NETWORK_COLORS } from "./networkMeta";

// Regression coverage: `unichain`/`ink` are 2 of the 6 networks the ingestion
// layer actually feeds (root CLAUDE.md §1) but had no NETWORK_COLORS entry,
// falling back to generic gray — indistinguishable from each other in chips/
// dots/sparklines (recorded LOW in docs/notes-ingestion-audit-stress-test.md
// item #14). Now directly relevant to cross-chain legibility: a cross-chain
// row can render a unichain/ink destination dot right next to its source dot.
describe("networkColor", () => {
  it("has a distinct, real color for every shipped network — including unichain and ink", () => {
    const keys = ["base", "arbitrum", "optimism", "polygon", "unichain", "ink"];
    const colors = keys.map((k) => networkColor(k));
    for (const c of colors) {
      expect(c).not.toBe("var(--color-ink-faint)"); // the generic-gray fallback
    }
    // No two shipped networks should collide on the same categorical slot.
    expect(new Set(colors).size).toBe(keys.length);
  });

  it("still falls back to the faint-gray default for a genuinely unknown key", () => {
    expect(networkColor("some-future-chain")).toBe("var(--color-ink-faint)");
  });

  it("exposes unichain/ink in the exported map directly", () => {
    expect(NETWORK_COLORS.unichain).toBe("var(--color-net-unichain)");
    expect(NETWORK_COLORS.ink).toBe("var(--color-net-ink)");
  });
});
