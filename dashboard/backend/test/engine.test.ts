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

  it("does not broadcast, wedge, or swallow when the gated live executor refuses (manual)", async () => {
    const store = new SettingsStore({ minProfitUsd: 0, minProfitBps: 0, executionMode: "live" });
    const provider = new StubProvider([makeOpportunity({ netProfitUsd: 175 })]);
    const engine = new ArbitrageEngine(store, provider, pairExecutors());
    const alerts: string[] = [];
    engine.on("alert", (a) => alerts.push(a.message));

    await engine.tick();
    const opp = engine.getOpportunities()[0]!;
    expect(opp.status).toBe("new");

    // The safety gate holds: LiveExecutor refuses to broadcast, so the call
    // rejects — nothing is ever sent on-chain.
    await expect(engine.executeOpportunity(opp.id)).rejects.toThrow(/not enabled/i);

    // ...and the opportunity is NOT left wedged in "executing" (prune skips that
    // status, so a wedged row would linger forever).
    const after = engine.getOpportunities().find((o) => o.id === opp.id);
    expect(after).toBeDefined();
    expect(after!.status).toBe("new");
    expect(engine.getStats().executed).toBe(0);
    // The refusal reason is surfaced to the UI as an alert.
    expect(alerts.some((m) => /refus/i.test(m))).toBe(true);

    // Because the status was reset, it prunes normally once expired.
    after!.expiresAt = Date.now() - 1;
    provider.setBatch([]);
    await engine.tick();
    expect(engine.getOpportunities().find((o) => o.id === opp.id)).toBeUndefined();
  });

  it("rejects an opportunity on a network the user hasn't enabled, on every provider", async () => {
    // Regression: previously only SimulatedProvider itself respected `networks` —
    // the engine-level filter must reject it too, so ExternalProvider (the real
    // production data source) can't silently bypass the Settings UI's network
    // chips just because it doesn't consult them itself.
    const store = new SettingsStore({ networks: ["base"], minProfitUsd: 0, minProfitBps: 0 });
    const provider = new StubProvider([makeOpportunity({ network: "optimism", chainId: 10 })]);
    const engine = new ArbitrageEngine(store, provider, pairExecutors());

    await engine.tick();
    expect(engine.getOpportunities()).toHaveLength(0);
  });

  it("rejects an opportunity routed through a DEX the user hasn't enabled", async () => {
    const store = new SettingsStore({
      dexes: ["uniswap-v3"], // "aerodrome" (used by the fixture's second leg) excluded
      minProfitUsd: 0,
      minProfitBps: 0,
    });
    const provider = new StubProvider([makeOpportunity()]);
    const engine = new ArbitrageEngine(store, provider, pairExecutors());

    await engine.tick();
    expect(engine.getOpportunities()).toHaveLength(0);
  });

  it("rejects an opportunity touching a token outside the configured universe", async () => {
    const store = new SettingsStore({
      tokens: ["USDC", "USDT"], // "WETH" (the fixture's traded asset) excluded
      minProfitUsd: 0,
      minProfitBps: 0,
    });
    const provider = new StubProvider([makeOpportunity()]);
    const engine = new ArbitrageEngine(store, provider, pairExecutors());

    await engine.tick();
    expect(engine.getOpportunities()).toHaveLength(0);
  });

  it("rejects an opportunity denominated in a different asset than baseToken", async () => {
    const store = new SettingsStore({
      baseToken: "USDC",
      tokens: ["USDC", "WETH"],
      minProfitUsd: 0,
      minProfitBps: 0,
    });
    const provider = new StubProvider([makeOpportunity({ tokenIn: "WETH" })]);
    const engine = new ArbitrageEngine(store, provider, pairExecutors());

    await engine.tick();
    expect(engine.getOpportunities()).toHaveLength(0);
  });

  it("accepts an opportunity that matches every enabled network/DEX/token control", async () => {
    const store = new SettingsStore({ minProfitUsd: 0, minProfitBps: 0 });
    const provider = new StubProvider([makeOpportunity()]);
    const engine = new ArbitrageEngine(store, provider, pairExecutors());

    await engine.tick();
    expect(engine.getOpportunities()).toHaveLength(1);
  });

  it("a manual execute respects maxConcurrentTrades, not just auto-execute", async () => {
    // Regression: previously maxConcurrentTrades was only checked inside the
    // auto-execute loop; a direct executeOpportunity call (manual click or
    // POST /api/execute/:id) sailed through regardless.
    const store = new SettingsStore({
      minProfitUsd: 0,
      minProfitBps: 0,
      maxConcurrentTrades: 1,
    });
    const provider = new StubProvider([
      makeOpportunity({ netProfitUsd: 175 }),
      makeOpportunity({ netProfitUsd: 175, network: "arbitrum", chainId: 42161 }),
    ]);
    const engine = new ArbitrageEngine(store, provider, pairExecutors());
    await engine.tick();
    const [first, second] = engine.getOpportunities();

    // Hold the first execution in flight (don't await) to occupy the one slot,
    // then attempt a second manual execute while it's still in flight.
    const firstExec = engine.executeOpportunity(first!.id);
    await expect(engine.executeOpportunity(second!.id)).rejects.toThrow(/concurrent trades/i);
    await firstExec;
  });

  it("a manual execute respects the per-network cooldown, not just auto-execute", async () => {
    const store = new SettingsStore({
      minProfitUsd: 0,
      minProfitBps: 0,
      cooldownMs: 60_000,
    });
    const provider = new StubProvider([makeOpportunity({ netProfitUsd: 175 })]);
    const engine = new ArbitrageEngine(store, provider, pairExecutors());
    await engine.tick();
    const opp = engine.getOpportunities()[0]!;

    await engine.executeOpportunity(opp.id);
    // Re-tick so a fresh "new" opportunity on the same (still-cooling-down)
    // network is active, then attempt to execute it manually.
    await engine.tick();
    const next = engine.getOpportunities().find((o) => o.status === "new");
    expect(next).toBeDefined();
    await expect(engine.executeOpportunity(next!.id)).rejects.toThrow(/cooldown/i);
  });

  it("a manual execute respects the daily loss circuit breaker, not just auto-execute", async () => {
    const store = new SettingsStore({
      minProfitUsd: 0,
      minProfitBps: 0,
      maxDailyLossUsd: 100,
    });
    const provider = new StubProvider([makeOpportunity()]);
    const engine = new ArbitrageEngine(store, provider, pairExecutors());
    await engine.tick();
    const opp = engine.getOpportunities()[0]!;

    // Simulate the day's realized losses already having tripped the breaker.
    (engine as unknown as { stats: { dailyPnlUsd: number } }).stats.dailyPnlUsd = -150;
    await expect(engine.executeOpportunity(opp.id)).rejects.toThrow(/daily loss limit/i);
  });

  it("auto-execute in gated live mode neither throws nor raises an unhandled rejection", async () => {
    const store = new SettingsStore({
      minProfitUsd: 0,
      minProfitBps: 0,
      executionMode: "live",
      autoExecute: true,
      cooldownMs: 0,
    });
    const provider = new StubProvider([makeOpportunity({ netProfitUsd: 175 })]);
    const engine = new ArbitrageEngine(store, provider, pairExecutors());

    const rejections: unknown[] = [];
    const onRejection = (r: unknown) => rejections.push(r);
    process.on("unhandledRejection", onRejection);
    try {
      // The scan tick resolves cleanly even though every auto-exec attempt hits
      // the refusing LiveExecutor.
      await expect(engine.tick()).resolves.toBeUndefined();
      // Let the fire-and-forget execution promises settle.
      await new Promise((r) => setTimeout(r, 10));
    } finally {
      process.off("unhandledRejection", onRejection);
    }

    expect(rejections).toHaveLength(0);
    // Gate held: nothing executed/broadcast, and nothing left wedged.
    expect(engine.getStats().executed).toBe(0);
    expect(engine.getOpportunities().every((o) => o.status !== "executing")).toBe(true);
  });
});
