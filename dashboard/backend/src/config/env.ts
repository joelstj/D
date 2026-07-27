import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { config as loadDotenv } from "dotenv";

/**
 * Load environment from the consolidated `.env` files. The whole product is
 * driven by ONE master `.env` at the repo root; a component-local `dashboard/.env`
 * may override it, and real process env (including what the launcher injects)
 * overrides both. dotenv never overrides an already-set key, so we load from the
 * nearest `.env` upward to the repo-root master — nearest wins.
 *
 * Anchoring on this module's own location (not the CWD) keeps it correct whether
 * the backend runs from source (`tsx`) or the bundled `dist/index.js`, and
 * regardless of the working directory the launcher starts it in.
 */
function loadConsolidatedEnv(): void {
  let dir = dirname(fileURLToPath(import.meta.url));
  for (let i = 0; i < 8; i++) {
    const candidate = resolve(dir, ".env");
    if (existsSync(candidate)) loadDotenv({ path: candidate });
    // The repo root holds the sibling component folders and the master `.env`.
    if (existsSync(resolve(dir, "engine")) && existsSync(resolve(dir, "dashboard"))) break;
    const parent = dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
}

loadConsolidatedEnv();

/**
 * Central environment resolution. Every value has a safe default so the server
 * boots with zero configuration in simulation mode.
 */
export interface Env {
  port: number;
  corsOrigins: string[];
  dataSource: "simulated" | "live" | "external";
  executionMode: "paper" | "live";
  rpcUrls: Record<string, string | undefined>;
  /** WebSocket URL of the Rust ingestion layer's output sink (real detections). */
  ingestFeedUrl: string;
  /** Optional deployed FlashLoanArbitrage address for the read-only execution
   *  latency probe's `staticCall`. Unset → the probe times RPC reads only. */
  executorAddress?: string;
  /** Optional chain key to pin the execution latency probe to (else first RPC). */
  executionProbeChain?: string;
  /** When set, the built frontend at this dir is served on the same origin as
   *  the API (single-port desktop / .exe mode). Undefined → API only. */
  staticDir?: string;
  version: string;
}

function num(value: string | undefined, fallback: number): number {
  const n = value ? Number(value) : NaN;
  return Number.isFinite(n) ? n : fallback;
}

function resolveDataSource(v: string | undefined): Env["dataSource"] {
  if (v === "live") return "live";
  if (v === "external") return "external";
  return "simulated";
}

export function loadEnv(): Env {
  const dataSource = resolveDataSource(process.env.DATA_SOURCE);
  const executionMode = process.env.EXECUTION_MODE === "live" ? "live" : "paper";
  return {
    port: num(process.env.PORT, 8787),
    corsOrigins: (process.env.CORS_ORIGIN || "http://localhost:5173")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean),
    dataSource,
    executionMode,
    rpcUrls: {
      base: process.env.RPC_URL_BASE,
      arbitrum: process.env.RPC_URL_ARBITRUM,
      optimism: process.env.RPC_URL_OPTIMISM,
      polygon: process.env.RPC_URL_POLYGON,
      unichain: process.env.RPC_URL_UNICHAIN,
      ink: process.env.RPC_URL_INK,
    },
    // The Rust ingestion layer's ws output sink. Used when DATA_SOURCE=external.
    ingestFeedUrl: process.env.INGEST_FEED_URL || "ws://127.0.0.1:9001",
    executorAddress: process.env.FLASH_LOAN_EXECUTOR_ADDRESS || undefined,
    executionProbeChain: process.env.EXECUTION_PROBE_CHAIN || undefined,
    staticDir: process.env.SERVE_STATIC_DIR || undefined,
    version: process.env.npm_package_version || "0.1.0",
  };
}
