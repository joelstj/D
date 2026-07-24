import { createServer, type Server as HttpServer } from "node:http";
import path from "node:path";
import express, { type Express } from "express";
import cors from "cors";
import { loadEnv, type Env } from "./config/env";
import { SettingsStore } from "./settings/store";
import type { Settings } from "./settings/schema";
import { SimulatedProvider } from "./arbitrage/providers/simulated";
import { LiveProvider } from "./arbitrage/providers/live";
import { ExternalProvider } from "./arbitrage/providers/external";
import type { OpportunityProvider } from "./arbitrage/providers/provider";
import { PaperExecutor, LiveExecutor, type Executor } from "./arbitrage/executor";
import { ArbitrageEngine } from "./arbitrage/engine";
import { WsHub } from "./ws/hub";
import { createApiRouter } from "./routes/api";
import { NETWORKS } from "./arbitrage/networks";
import { createLogger } from "./util/logger";

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
}

/**
 * Choose the opportunity source from the resolved environment:
 *  - `external`  → real detections from the Rust ingestion layer over WebSocket
 *                  (the wired, production path for the merged bot);
 *  - `live`      → direct on-chain quoting via RPC (currently returns none);
 *  - `simulated` → zero-config paper stream (default).
 */
function selectProvider(env: Env): OpportunityProvider {
  switch (env.dataSource) {
    case "external":
      return new ExternalProvider(env.ingestFeedUrl);
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
  const store = new SettingsStore({
    executionMode: env.executionMode,
    ...opts.initialSettings,
  });

  const provider = opts.provider ?? selectProvider(env);

  // Both executors always exist; the engine selects one per execution based on
  // the live `executionMode` setting. Live execution is gated inside LiveExecutor.
  const executors = opts.executors ?? { paper: new PaperExecutor(), live: new LiveExecutor() };

  const engine = new ArbitrageEngine(store, provider, executors);

  const app = express();
  app.use(cors({ origin: env.corsOrigins.length ? env.corsOrigins : true }));
  app.use(express.json({ limit: "256kb" }));

  const httpServer = createServer(app);

  const hub = new WsHub(httpServer, () => ({
    settings: store.get(),
    networks: NETWORKS,
    opportunities: engine.getOpportunities().slice(0, 100),
    stats: engine.getStats(),
  }));

  app.use(
    "/api",
    createApiRouter({ store, engine, env, startedAt, clientCount: () => hub.clientCount() }),
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

  // Bridge engine + settings events onto the websocket fan-out.
  engine.on("opportunity", (opp) => hub.broadcast("opportunity", opp));
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
