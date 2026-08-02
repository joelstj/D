import { existsSync, readFileSync, writeFileSync } from "node:fs";

/**
 * Minimal, comment-preserving `.env` upserter for recording deployed contract
 * addresses back into the master `.env`.
 *
 * ## Safety
 * This ONLY ever writes the specific address keys it is handed (all non-secret,
 * on-chain-public contract addresses). It never writes, reads back, or relocates
 * a secret: `PRIVATE_KEY`, `ETHERSCAN_API_KEY`, RPC keys, etc. are left exactly
 * as the operator wrote them. Existing keys are edited in place; unknown keys are
 * appended under a clearly-labelled managed block. Comments and ordering are
 * preserved so a hand-maintained `.env` stays readable.
 */

/** Keys this module is allowed to write. A guard against ever persisting a secret. */
const MANAGED_PREFIXES = [
  "FLASH_LOAN_EXECUTOR_ADDRESS",
  "CROSSCHAIN_EXECUTOR_ADDRESS",
  "EXECUTION_PROBE_CHAIN",
];

/** A value is a plain 0x address or a short chain key — never a secret. */
function assertManaged(key: string): void {
  if (!MANAGED_PREFIXES.some((p) => key === p || key.startsWith(p + "_"))) {
    throw new Error(`envFile: refusing to write non-managed key "${key}"`);
  }
}

/** Parse `.env` text into ordered lines, tracking which lines are `KEY=...`. */
function splitLines(text: string): string[] {
  return text.length === 0 ? [] : text.replace(/\r\n/g, "\n").split("\n");
}

function keyOfLine(line: string): string | null {
  const m = /^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=/.exec(line);
  return m ? (m[1] ?? null) : null;
}

export interface UpsertOptions {
  /** Path to the `.env` to edit/create. */
  file: string;
  /** Keys → values to set. Only managed keys are permitted. */
  values: Record<string, string>;
  /**
   * When the file is missing, seed it from this template first (the tracked
   * `.env.example`). If the template is also missing, a minimal header is used.
   */
  seedFrom?: string;
  /**
   * Keys to set only if not already present with a non-empty value (used for the
   * singular `FLASH_LOAN_EXECUTOR_ADDRESS` / `EXECUTION_PROBE_CHAIN` so we never
   * clobber an operator's explicit probe-chain choice). Default: none.
   */
  onlyIfEmpty?: string[];
}

export interface UpsertResult {
  created: boolean;
  updatedKeys: string[];
  skippedKeys: string[];
}

/**
 * Upsert `values` into a `.env` file, preserving comments/order and never
 * touching secrets. Creates the file (optionally from a template) when absent.
 */
export function upsertEnv(opts: UpsertOptions): UpsertResult {
  const onlyIfEmpty = new Set(opts.onlyIfEmpty ?? []);
  for (const key of Object.keys(opts.values)) assertManaged(key);

  let created = false;
  let text: string;
  if (existsSync(opts.file)) {
    text = readFileSync(opts.file, "utf8");
  } else {
    created = true;
    text =
      opts.seedFrom && existsSync(opts.seedFrom)
        ? readFileSync(opts.seedFrom, "utf8")
        : "# L2 Arbitrage Flash-Loan Bot — .env (git-ignored). Managed address keys below.\n";
  }

  const lines = splitLines(text);
  const presentValue = new Map<string, string>();
  for (const line of lines) {
    const k = keyOfLine(line);
    if (k) {
      const eq = line.indexOf("=");
      presentValue.set(k, line.slice(eq + 1).trim());
    }
  }

  const updatedKeys: string[] = [];
  const skippedKeys: string[] = [];
  const toAppend: string[] = [];

  for (const [key, rawValue] of Object.entries(opts.values)) {
    const value = String(rawValue).trim();
    const existing = presentValue.get(key);
    if (onlyIfEmpty.has(key) && existing !== undefined && existing !== "") {
      skippedKeys.push(key);
      continue;
    }
    if (existing !== undefined) {
      // Replace the value on the existing line, keeping any inline layout.
      for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        if (line !== undefined && keyOfLine(line) === key) {
          lines[i] = `${key}=${value}`;
          break;
        }
      }
      updatedKeys.push(key);
    } else {
      toAppend.push(`${key}=${value}`);
      updatedKeys.push(key);
    }
  }

  let out = lines.join("\n");
  if (toAppend.length) {
    if (out.length && !out.endsWith("\n")) out += "\n";
    out +=
      "\n# ── Deployed contract addresses (managed by the dashboard Contracts panel) ──\n" +
      toAppend.join("\n") +
      "\n";
  }
  if (!out.endsWith("\n")) out += "\n";

  writeFileSync(opts.file, out, "utf8");
  return { created, updatedKeys, skippedKeys };
}

/** Uppercase network key → env-var suffix (e.g. `arbitrum` → `ARBITRUM`). */
export function envSuffix(networkKey: string): string {
  return networkKey.toUpperCase().replace(/[^A-Z0-9]+/g, "_");
}
