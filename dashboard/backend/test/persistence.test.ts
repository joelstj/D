import { randomUUID } from "node:crypto";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { DEFAULT_SETTINGS } from "../src/settings/schema";
import { loadPersistedSettings, savePersistedSettings } from "../src/settings/persistence";

describe("settings persistence", () => {
  let dir: string;

  afterEach(() => {
    if (dir) rmSync(dir, { recursive: true, force: true });
  });

  function tempFile(): string {
    dir = mkdtempSync(join(tmpdir(), "l2arb-persist-"));
    return join(dir, "settings.json");
  }

  it("returns null when no file exists yet (first run)", () => {
    const file = join(tmpdir(), `l2arb-missing-${randomUUID()}.json`);
    expect(loadPersistedSettings(file)).toBeNull();
  });

  it("round-trips a saved settings object exactly", () => {
    const file = tempFile();
    const settings = { ...DEFAULT_SETTINGS, loanAmountUsd: 77_777, minProfitUsd: 12 };

    savePersistedSettings(file, settings);
    const loaded = loadPersistedSettings(file);

    expect(loaded).toEqual(settings);
  });

  it("creates the parent directory on first save", () => {
    dir = mkdtempSync(join(tmpdir(), "l2arb-persist-"));
    const nested = join(dir, "nested", "deeper", "settings.json");

    savePersistedSettings(nested, DEFAULT_SETTINGS);

    expect(JSON.parse(readFileSync(nested, "utf8"))).toEqual(DEFAULT_SETTINGS);
  });

  it("ignores a corrupt (unparsable) file rather than throwing", () => {
    const file = tempFile();
    writeFileSync(file, "{not valid json", "utf8");
    expect(loadPersistedSettings(file)).toBeNull();
  });

  it("ignores a file that fails schema validation", () => {
    const file = tempFile();
    writeFileSync(file, JSON.stringify({ minProfitUsd: -999 }), "utf8");
    expect(loadPersistedSettings(file)).toBeNull();
  });

  it("loads a partial file (older version missing a since-added field)", () => {
    const file = tempFile();
    writeFileSync(file, JSON.stringify({ loanAmountUsd: 42_000 }), "utf8");
    expect(loadPersistedSettings(file)).toEqual({ loanAmountUsd: 42_000 });
  });
});
