import { describe, it, expect } from "vitest";
import { formatUsd, formatBps, formatPct, shortAddress, timeAgo } from "./format";

describe("format", () => {
  it("formats USD with adaptive precision", () => {
    expect(formatUsd(1234.5)).toBe("$1,235");
    expect(formatUsd(12.3456)).toBe("$12.35");
    expect(formatUsd(0.0123)).toBe("$0.0123");
  });

  it("adds an explicit sign when asked", () => {
    expect(formatUsd(50, { sign: true })).toBe("+$50.00");
    expect(formatUsd(-50)).toBe("-$50.00");
  });

  it("formats compact USD", () => {
    expect(formatUsd(50000, { compact: true })).toBe("$50K");
    expect(formatUsd(2500000, { compact: true })).toBe("$2.5M");
  });

  it("formats basis points and percentages", () => {
    expect(formatBps(12.34)).toBe("12.3 bps");
    expect(formatPct(0.5)).toBe("50%");
    expect(formatPct(0.1234, 1)).toBe("12.3%");
  });

  it("shortens addresses", () => {
    expect(shortAddress("0x1234567890abcdef1234567890abcdef12345678")).toBe("0x1234…5678");
    expect(shortAddress(undefined)).toBe("");
  });

  it("renders relative time", () => {
    const now = 1_000_000;
    expect(timeAgo(now, now)).toBe("now");
    expect(timeAgo(now - 5000, now)).toBe("5s ago");
    expect(timeAgo(now - 120_000, now)).toBe("2m ago");
  });
});
