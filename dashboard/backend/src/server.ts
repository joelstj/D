import { createServer, type Server as HttpServer } from "node:http";
import { performance } from "node:perf_hooks";
import path from "node:path";
import express, { type Express } from "express";
import cors from "cors";
import { loadEnv, type Env } from "./config/env";
import { SettingsStore } from "./settings/store";
import type { Settings } from "./settings/schema";
import {
  defaultSettingsPath,
  loadPersistedSettings,
  savePersistedSettings,
} from "./settings/persistence";
import { SimulatedProvider } from "./arbitrage/providers/simulated";
import { LiveProvider } from "./arbitrage/providers/live";
import { ExternalProvider } from "./arbitrage/providers/external";
import type { OpportunityProvider } from "./arbitrage/providers/provider";
import { PaperExecutor, LiveExecutor, type Executor } from "./arbitrage/executor";
import { ArbitrageEngine } from "./arbitrage/engine";
import { LatencyMonitor } from "./arbitrage/latency";
import { ExecutionLatencyProbe } from "./arbitrage/executionLatency";
import { WsHub } from "./ws/hub";
import { createApiRouter } from "./routes/api";
import { NETWORKS } from "./arbitrage/networks";
import { createLogger } from "./util/logger";

/** Min interval between `latency` snapshot broadcasts (throttle the fan-out). */
const LATENCY_BROADCAST_MS = 500;

const log = createLogger("server");

export interface AppHandles {
  app: Express;
  httpServer: HttpServer;
  hub: WsHub;
  engine: ArbitrageEngine;
  store: SettingsStore;
  env: Env;
  start: (port?: number) => Promise<number>;
  stop: () => Promise<void>;
}

export interface BuildOptions {
  env?: Env;
  initialSettings?: Partial<Settings>;
  provider?: OpportunityProvider;
  executors?: { paper: Executor; live: Executor };
  /** When false the engine is constructed but its scan loop is not started (tests). */
  autoStartEngine?: boolean;
  /** Where settings persist across restarts. Defaults to `SETTINGS_FILE` or
   *  `<backend>/.data/settings.json`. Tests should pass an isolated path so a
   *  stray real file from local dev never leaks into a test run. */
  settingsFile?: string;
}

/**
 * Choose the opportunity source from the resolved environment:
 *  - `external`  → real detections from the Rust ingestion layer over WebSocket
 *                  (the wired, production path for the merged bot);
 *  - `live`      → direct on-chain quoting via RPC (currently returns none);
 *  - `simulated` → zero-config paper stream (default).
 */
function selectProvider(env: Env, latency: LatencyMonitor): OpportunityProvider {
  switch (env.dataSource) {
    case "external":
      return new ExternalProvider(env.ingestFeedUrl, { latency });
    case "live":
      return new LiveProvider(env.rpcUrls);
    default:
      return new SimulatedProvider();
  }
}

/**
 * Compose the full application: settings store, engine, REST router, and the
 * WebSocket hub — with every engine event wired into the hub so all clients see
 * updates live. Returns handles for both production (`index.ts`) and tests.
 */
export function buildServer(opts: BuildOptions = {}): AppHandles {
  const env = opts.env ?? loadEnv();
  const startedAt = Date.now();

  // Restore whatever was last PATCHed before the previous restart (P0 in
  // ralph/backlog.md — every adjustable setting used to silently revert to
  // schema defaults on every restart). `executionMode` is the one exception:
  // it's always re-seeded from the operator's current EXECUTION_MODE, the
  // boot-time safety posture, rather than resuming a possibly-stale persisted
  // value — see CLAUDE.md §2 invariant 3 (paper-by-default).
  const settingsFile = opts.settingsFile ?? process.env.SETTINGS_FILE ?? defaultSettingsPath();
  const persisted = loadPersistedSettings(settingsFile) ?? {};
  const store = new SettingsStore({
    ...persisted,
    executionMode: env.executionMode,
    ...opts.initialSettings,
  });
  store.onChange(({ settings }) => savePersistedSettings(settingsFile, settings));

  // End-to-end latency aggregator (ingestion → engine → dashboard) and the
  // separate, read-only on-chain execution-readiness probe.
  const latency = new LatencyMonitor();
  const executionProbe = new ExecutionLatencyProbe({
    rpcUrls: env.rpcUrls,
    executorAddress: env.executorAddress,
    chain: env.executionProbeChain,
  });

  const provider = opts.provider ?? selectProvider(env, latency);

  // Both executors always exist; the engine selects one per execution based on
  // the live `executionMode` setting. Live execution is gated inside LiveExecutor.
  const executors = opts.executors ?? { paper: new PaperExecutor(), live: new LiveExecutor() };

  const engine = new ArbitrageEngine(store, provider, executors, latency);

  const app = express();
  app.use(cors({ origin: env.corsOrigins.length ? env.corsOrigins : true }));
  app.use(express.json({ limit: "256kb" }));

  const httpServer = createServer(app);

  const hub = new WsHub(httpServer, () => ({
    settings: store.get(),
    networks: NETWORKS,
    opportunities: engine.getOpportunities().slice(0, 100),
    stats: engine.getStats(),
    latency: latency.snapshot(),
  }));

  app.use(
    "/api",
    createApiRouter({
      store,
      engine,
      env,
      startedAt,
      clientCount: () => hub.clientCount(),
      latency,
      executionProbe,
    }),
  );

  // Optional single-origin mode: serve the built frontend (frontend/dist) on the
  // same server as the API + WebSocket. The launcher / .exe sets SERVE_STATIC_DIR
  // so the whole app is reachable at one URL with no nginx. API and /ws are left
  // untouched; everything else falls back to the SPA entrypoint.
  if (env.staticDir) {
    const staticDir = env.staticDir;
    app.use(express.static(staticDir));
    app.get("*", (req, res, next) => {
      if (req.path.startsWith("/api") || req.path === "/ws") return next();
      res.sendFile(path.join(staticDir, "index.html"));
    });
    log.info(`serving frontend from ${staticDir}`);
  }

  // Throttled push of the latency snapshot so the UI HUD updates live without a
  // frame per opportunity.
  let lastLatencyPush = 0;
  const maybeBroadcastLatency = () => {
    const t = Date.now();
    if (t - lastLatencyPush >= LATENCY_BROADCAST_MS) {
      lastLatencyPush = t;
      hub.broadcast("latency", latency.snapshot());
    }
  };

  // Bridge engine + settings events onto the websocket fan-out. The opportunity
  // bridge also closes the latency trace: time the fan-out and, when the batch
  // carried an ingestion origin anchor, record the single-host end-to-end.
  engine.on("opportunity", (opp) => {
    const fanoutStart = performance.now();
    hub.broadcast("opportunity", opp);
    latency.record("dashboard", "fanout", performance.now() - fanoutStart);
    if (typeof opp.originWallMs === "number" && opp.originWallMs > 0) {
      latency.recordEndToEnd(Date.now() - opp.originWallMs);
    }
    maybeBroadcastLatency();
  });
  engine.on("opportunity:remove", (id) => hub.broadcast("opportunity:remove", { id }));
  engine.on("execution", (result) => hub.broadcast("execution", result));
  engine.on("stats", (stats) => hub.broadcast("stats", stats));
  engine.on("alert", (alert) => hub.broadcast("alert", alert));
  store.onChange(({ settings }) => hub.broadcast("settings", settings));

  const handles: AppHandles = {
    app,
    httpServer,
    hub,
    engine,
    store,
    env,
    async start(port = env.port) {
      if (opts.autoStartEngine !== false) await engine.start();
      await new Promise<void>((resolve) => httpServer.listen(port, resolve));
      const addr = httpServer.address();
      const actual = typeof addr === "object" && addr ? addr.port : port;
      log.info(`API listening on http://localhost:${actual} (ws: /ws)`);
      return actual;
    },
    async stop() {
      await engine.stop();
      hub.close();
      await new Promise<void>((resolve) => httpServer.close(() => resolve()));
    },
  };

  return handles;
}
