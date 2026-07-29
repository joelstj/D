import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createLogger } from "../util/logger";
import { SettingsSchema, type Settings } from "./schema";

const log = createLogger("settings");

/**
 * The backend package root (nearest ancestor of this module holding a
 * `package.json`), found by walking up from wherever this module actually runs
 * from — `src/settings/` under `tsx`, or a flat bundled `dist/index.js` in
 * production — so the resolved path is the same regardless of run mode.
 */
function backendRoot(): string {
  let dir = dirname(fileURLToPath(import.meta.url));
  for (let i = 0; i < 8; i++) {
    if (existsSync(resolve(dir, "package.json"))) return dir;
    const parent = dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  return dirname(fileURLToPath(import.meta.url));
}

/** Default persistence path: `<backend>/.data/settings.json`, overridable via
 *  `SETTINGS_FILE` (see config/env.ts) for a relocated state directory. */
export function defaultSettingsPath(): string {
  return process.env.SETTINGS_FILE || resolve(backendRoot(), ".data", "settings.json");
}

/**
 * Load a previously-persisted settings patch from disk. Returns `null` when the
 * file doesn't exist yet (first run) or can't be parsed as valid settings — a
 * missing/corrupt file is never fatal and never fabricates a value; the caller
 * falls back to `DEFAULT_SETTINGS` for anything absent, same as any other
 * partial patch (`SettingsStore`'s constructor already merges onto defaults).
 *
 * Parsed with `.partial()` (not the full schema) so a file written by an older
 * version — missing a since-added field — still loads instead of failing whole.
 */
export function loadPersistedSettings(filePath: string): Partial<Settings> | null {
  let json: unknown;
  try {
    json = JSON.parse(readFileSync(filePath, "utf8"));
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code !== "ENOENT") {
      log.warn(`ignoring unreadable settings file at ${filePath}: ${String(err)}`);
    }
    return null;
  }
  const parsed = SettingsSchema.partial().safeParse(json);
  if (!parsed.success) {
    log.warn(`ignoring invalid persisted settings at ${filePath}: ${parsed.error.message}`);
    return null;
  }
  return parsed.data;
}

/**
 * Persist the full settings object to disk. Best-effort: a write failure (e.g.
 * a read-only filesystem) is logged, never thrown — losing persistence must
 * never crash the running engine, only mean a setting reverts on next restart.
 */
export function savePersistedSettings(filePath: string, settings: Settings): void {
  try {
    mkdirSync(dirname(filePath), { recursive: true });
    writeFileSync(filePath, JSON.stringify(settings, null, 2), "utf8");
  } catch (err) {
    log.warn(`failed to persist settings to ${filePath}: ${String(err)}`);
  }
}
