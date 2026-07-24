import { describe, it, expect } from "vitest";
import { SettingsStore } from "../src/settings/store";
import { ArbitrageEngine } from "../src/arbitrage/engine";
import { makeOpportunity, StubProvider, pairExecutors } from "./helpers";

describe("ArbitrageEngine", () => {
  it("surfaces opportunities that clear the profit thresholds", async () => {
    const store = new SettingsStore({ minProfitUsd: 100, minProfitBps: 10 });
    const provider = new StubProvider([makeOpportunity({ netProfitUsd: 175, profitBps: 35 })]);
    const engine = new ArbitrageEngine(store, provider, pairExecutors());

    await engine.tick();

    const opps = engine.getOpportunities();
    expect(opps.length).toBeGreaterThan(0);
    expect(opps[0]!.netProfitUsd).toBe(175);
  });

  it("filters out opportunities below the profit threshold", async () => {
    const store = new SettingsStore({ minProfitUsd: 1000 });
    const provider = new StubProvider([makeOpportunity({ netProfitUsd: 5, profitBps: 1 })]);
    const engine = new ArbitrageEngine(store, provider, pairExecutors());

    await engine.tick();
    expect(engine.getOpportunities()).toHaveLength(0);
  });

  it("does not scan when the engine is disabled", async () => {
    const store = new SettingsStore({ engineEnabled: false });
    const provider = new StubProvider([makeOpportunity()]);
    const engine = new ArbitrageEngine(store, provider, pairExecutors());

    await engine.tick();
    expect(engine.getOpportunities()).toHaveLength(0);
    expect(engine.getStats().scans).toBe(0);
  });

  it("executes an opportunity and records the result in stats", async () => {
    const store = new SettingsStore({ minProfitUsd: 0, minProfitBps: 0 });
    const provider = new StubProvider([makeOpportunity({ netProfitUsd: 175 })]);
    const engine = new ArbitrageEngine(store, provider, pairExecutors());

    await engine.tick();
    const opp = engine.getOpportunities()[0]!;
    const result = await engine.executeOpportunity(opp.id);

    expect(result.opportunityId).toBe(opp.id);
    expect(["filled", "reverted"]).toContain(result.status);
    expect(engine.getStats().executed).toBe(1);
    // Executed opportunities are removed from the active set.
    expect(engine.getOpportunities().find((o) => o.id === opp.id)).toBeUndefined();
  });

  it("prunes expired opportunities on the next tick", async () => {
    const store = new SettingsStore({ minProfitUsd: 0, minProfitBps: 0 });
    const provider = new StubProvider([makeOpportunity({ expiresAt: Date.now() + 10_000 })]);
    const engine = new ArbitrageEngine(store, provider, pairExecutors());

    // First tick adds a live opportunity.
    await engine.tick();
    expect(engine.getOpportunities().length).toBeGreaterThan(0);

    // Force expiry and stop the provider from emitting new ones, then re-tick.
    for (const o of engine.getOpportunities()) o.expiresAt = Date.now() - 1;
    provider.setBatch([]);
    await engine.tick();

    expect(engine.getOpportunities()).toHaveLength(0);
  });

  it("emits opportunity events for downstream broadcast", async () => {
    const store = new SettingsStore({ minProfitUsd: 0, minProfitBps: 0 });
    const engine = new ArbitrageEngine(
      store,
      new StubProvider([makeOpportunity()]),
      pairExecutors(),
    );
    const seen: string[] = [];
    engine.on("opportunity", (o) => seen.push(o.id));

    await engine.tick();
    expect(seen.length).toBeGreaterThan(0);
  });
});
