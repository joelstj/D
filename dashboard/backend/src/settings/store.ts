import { EventEmitter } from "node:events";
import {
  DEFAULT_SETTINGS,
  SettingsSchema,
  SettingsPatchSchema,
  type Settings,
  type SettingsPatch,
} from "./schema";
import { createLogger } from "../util/logger";

const log = createLogger("settings");

export interface SettingsChange {
  settings: Settings;
  changed: (keyof Settings)[];
}

/**
 * Holds the single source of truth for engine parameters. Any mutation is
 * validated as a *whole* object (so cross-field invariants hold) and then
 * broadcast synchronously to subscribers — this is the mechanism that makes
 * every GUI control take effect immediately in the running engine.
 */
export class SettingsStore extends EventEmitter {
  private current: Settings;

  constructor(initial: Partial<Settings> = {}) {
    super();
    this.current = SettingsSchema.parse({ ...DEFAULT_SETTINGS, ...initial });
  }

  get(): Settings {
    return this.current;
  }

  /** Replace the entire settings object (validated). */
  replace(next: unknown): Settings {
    const parsed = SettingsSchema.parse(next);
    this.commit(parsed);
    return this.current;
  }

  /** Merge a partial patch onto current settings (validated as a whole). */
  patch(patch: unknown): Settings {
    const validatedPatch: SettingsPatch = SettingsPatchSchema.parse(patch);
    const merged = SettingsSchema.parse({ ...this.current, ...validatedPatch });
    this.commit(merged);
    return this.current;
  }

  reset(): Settings {
    this.commit({ ...DEFAULT_SETTINGS });
    return this.current;
  }

  private commit(next: Settings) {
    const changed = diffKeys(this.current, next);
    this.current = next;
    if (changed.length === 0) return;
    log.info(`settings updated: ${changed.join(", ")}`);
    this.emit("change", { settings: next, changed } satisfies SettingsChange);
  }

  onChange(cb: (change: SettingsChange) => void): () => void {
    this.on("change", cb);
    return () => this.off("change", cb);
  }
}

function diffKeys(a: Settings, b: Settings): (keyof Settings)[] {
  const keys = Object.keys(b) as (keyof Settings)[];
  return keys.filter((k) => JSON.stringify(a[k]) !== JSON.stringify(b[k]));
}
