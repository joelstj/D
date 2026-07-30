import { randomUUID } from "node:crypto";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { buildServer, type AppHandles } from "../src/server";
import { loadEnv } from "../src/config/env";
import { makeOpportunity, StubProvider } from "./helpers";

/**
 * End-to-end proof of the persistence enhancement: a PATCH survives a full
 * server restart pointed at the same settings file (ralph/backlog.md P0 —
 * previously every adjustable setting silently reverted to schema defaults on
 * every restart), while `executionMode` specifically always re-seeds from the
 * operator's current EXECUTION_MODE rather than resuming a stale value.
 */
describe("settings restart persistence", () => {
  let dir: string;
  let settingsFile: string;
  let handles: AppHandles | null = null;

  function boot(env: Partial<ReturnType<typeof loadEnv>> = {}): AppHandles {
    return buildServer({
      env: { ...loadEnv(), dataSource: "simulated", executionMode: "paper", ...env },
      provider: new StubProvider([makeOpportunity()]),
      autoStartEngine: false,
      settingsFile,
    });
  }

  afterEach(async () => {
    if (handles) await handles.stop();
    handles = null;
    if (dir) rmSync(dir, { recursive: true, force: true });
  });

  it("a PATCHed setting survives a restart pointed at the same file", async () => {
    dir = mkdtempSync(join(tmpdir(), "l2arb-restart-"));
    settingsFile = join(dir, "settings.json");

    handles = boot();
    handles.store.patch({ loanAmountUsd: 88_888, minProfitBps: 3 });
    await handles.stop();

    handles = boot();
    expect(handles.store.get().loanAmountUsd).toBe(88_888);
    expect(handles.store.get().minProfitBps).toBe(3);
  });

  it("executionMode always re-seeds from EXECUTION_MODE, never resumes a stale persisted value", async () => {
    dir = mkdtempSync(join(tmpdir(), "l2arb-restart-"));
    settingsFile = join(dir, "settings.json");

    handles = boot({ executionMode: "paper" });
    handles.store.patch({ executionMode: "live" }); // e.g. toggled in the UI mid-session
    await handles.stop();

    // Restart with the operator's env back to (or still) paper — the safe
    // default must win, not the persisted "live".
    handles = boot({ executionMode: "paper" });
    expect(handles.store.get().executionMode).toBe("paper");
  });

  it("a fresh file location starts from defaults (first run)", () => {
    dir = mkdtempSync(join(tmpdir(), "l2arb-restart-"));
    settingsFile = join(dir, "settings-never-written.json");

    handles = boot();
    expect(handles.store.get().loanAmountUsd).toBe(50_000); // schema default
  });
});
