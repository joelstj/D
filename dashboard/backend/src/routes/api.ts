import { Router, type Response } from "express";
import { ZodError } from "zod";
import type { SettingsStore } from "../settings/store";
import type { ArbitrageEngine } from "../arbitrage/engine";
import type { LatencyMonitor } from "../arbitrage/latency";
import type { ExecutionLatencyProbe } from "../arbitrage/executionLatency";
import { NETWORKS, FLASH_LOAN_PROVIDERS } from "../arbitrage/networks";
import type { Env } from "../config/env";

export interface ApiDeps {
  store: SettingsStore;
  engine: ArbitrageEngine;
  env: Env;
  startedAt: number;
  clientCount: () => number;
  /** End-to-end pipeline latency aggregator (ingestion → engine → dashboard). */
  latency: LatencyMonitor;
  /** Separate read-only on-chain execution-readiness latency probe. */
  executionProbe: ExecutionLatencyProbe;
}

/**
 * Language-agnostic REST surface. Every mutation validates its payload and
 * routes through the SettingsStore / engine, so a curl call, a Python SDK, or
 * the React UI all take effect identically and immediately.
 */
export function createApiRouter(deps: ApiDeps): Router {
  const { store, engine, env, startedAt } = deps;
  const router = Router();

  router.get("/health", (_req, res) => {
    res.json({
      status: "ok",
      version: env.version,
      dataSource: env.dataSource,
      executionMode: env.executionMode,
      uptimeMs: Date.now() - startedAt,
      wsClients: deps.clientCount(),
    });
  });

  router.get("/networks", (_req, res) => res.json({ networks: NETWORKS }));

  router.get("/flash-loan-providers", (_req, res) =>
    res.json({
      providers: Object.entries(FLASH_LOAN_PROVIDERS).map(([key, v]) => ({ key, ...v })),
    }),
  );

  router.get("/settings", (_req, res) => res.json(store.get()));

  router.put("/settings", (req, res) =>
    guard(res, () => res.json(store.replace(req.body))),
  );

  router.patch("/settings", (req, res) =>
    guard(res, () => res.json(store.patch(req.body))),
  );

  router.post("/settings/reset", (_req, res) => res.json(store.reset()));

  router.get("/opportunities", (req, res) => {
    const limit = clampInt(req.query.limit, 1, 500, 100);
    const network = typeof req.query.network === "string" ? req.query.network : null;
    let opps = engine.getOpportunities();
    if (network) opps = opps.filter((o) => o.network === network);
    res.json({ opportunities: opps.slice(0, limit), total: opps.length });
  });

  router.get("/stats", (_req, res) => res.json(engine.getStats()));

  // End-to-end pipeline latency: per-component stage breakdown + the single-host
  // ingest → displayed measurement. Read-only observability.
  router.get("/latency", (_req, res) => res.json(deps.latency.snapshot()));

  // Separate on-chain execution-readiness latency (RPC + optional staticCall).
  // Strictly read-only — never broadcasts, never touches the live executor.
  router.get("/health/execution", (_req, res) =>
    guard(res, async () => res.json(await deps.executionProbe.get())),
  );

  router.post("/execute/:id", (req, res) =>
    guard(res, async () => {
      const result = await engine.executeOpportunity(req.params.id);
      res.json(result);
    }),
  );

  // Convenience toggle for the master engine switch (equivalent to PATCH settings).
  router.post("/engine/toggle", (req, res) =>
    guard(res, () => {
      const enabled =
        typeof req.body?.enabled === "boolean" ? req.body.enabled : !store.get().engineEnabled;
      res.json(store.patch({ engineEnabled: enabled }));
    }),
  );

  return router;
}

async function guard(res: Response, fn: () => unknown | Promise<unknown>) {
  try {
    await fn();
  } catch (err) {
    if (err instanceof ZodError) {
      res.status(400).json({ error: "validation_error", issues: err.issues });
    } else {
      res.status(400).json({ error: "bad_request", message: String((err as Error).message) });
    }
  }
}

function clampInt(value: unknown, min: number, max: number, fallback: number): number {
  const n = typeof value === "string" ? parseInt(value, 10) : NaN;
  if (!Number.isFinite(n)) return fallback;
  return Math.max(min, Math.min(max, n));
}
