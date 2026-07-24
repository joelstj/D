import "dotenv/config";

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
    },
    // The Rust ingestion layer's ws output sink. Used when DATA_SOURCE=external.
    ingestFeedUrl: process.env.INGEST_FEED_URL || "ws://127.0.0.1:9001",
    staticDir: process.env.SERVE_STATIC_DIR || undefined,
    version: process.env.npm_package_version || "0.1.0",
  };
}
