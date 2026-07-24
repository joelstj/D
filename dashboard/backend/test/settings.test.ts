import { describe, it, expect, vi } from "vitest";
import { SettingsStore } from "../src/settings/store";
import { DEFAULT_SETTINGS } from "../src/settings/schema";

describe("SettingsStore", () => {
  it("starts from validated defaults", () => {
    const store = new SettingsStore();
    expect(store.get()).toEqual(DEFAULT_SETTINGS);
  });

  it("applies a valid patch and reports changed keys", () => {
    const store = new SettingsStore();
    const changes: string[][] = [];
    store.onChange(({ changed }) => changes.push(changed as string[]));

    store.patch({ loanAmountUsd: 100_000, minProfitUsd: 50 });

    expect(store.get().loanAmountUsd).toBe(100_000);
    expect(store.get().minProfitUsd).toBe(50);
    expect(changes[0]).toContain("loanAmountUsd");
    expect(changes[0]).toContain("minProfitUsd");
  });

  it("does not emit when nothing changes", () => {
    const store = new SettingsStore();
    const spy = vi.fn();
    store.onChange(spy);
    store.patch({ loanAmountUsd: DEFAULT_SETTINGS.loanAmountUsd });
    expect(spy).not.toHaveBeenCalled();
  });

  it("rejects out-of-range values", () => {
    const store = new SettingsStore();
    expect(() => store.patch({ loanAmountUsd: -1 })).toThrow();
    expect(() => store.patch({ slippageBps: 99_999 })).toThrow();
    expect(() => store.patch({ scanIntervalMs: 10 })).toThrow();
  });

  it("rejects unknown keys (strict schema)", () => {
    const store = new SettingsStore();
    expect(() => store.patch({ notARealField: 1 })).toThrow();
  });

  it("resets back to defaults", () => {
    const store = new SettingsStore();
    store.patch({ loanAmountUsd: 999_999 });
    store.reset();
    expect(store.get()).toEqual(DEFAULT_SETTINGS);
  });
});
