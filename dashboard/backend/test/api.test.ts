import { describe, it, expect, beforeAll, afterAll } from "vitest";
import request from "supertest";
import { buildServer, type AppHandles } from "../src/server";
import { loadEnv } from "../src/config/env";
import { makeOpportunity, StubProvider } from "./helpers";

describe("REST API", () => {
  let handles: AppHandles;

  beforeAll(() => {
    handles = buildServer({
      env: { ...loadEnv(), dataSource: "simulated", executionMode: "paper" },
      provider: new StubProvider([makeOpportunity({ netProfitUsd: 175 })]),
      autoStartEngine: false,
      initialSettings: { minProfitUsd: 0, minProfitBps: 0 },
    });
  });

  afterAll(async () => {
    await handles.stop();
  });

  it("GET /api/health returns ok", async () => {
    const res = await request(handles.app).get("/api/health");
    expect(res.status).toBe(200);
    expect(res.body.status).toBe("ok");
    expect(res.body.executionMode).toBe("paper");
  });

  it("GET /api/networks lists supported L2s", async () => {
    const res = await request(handles.app).get("/api/networks");
    expect(res.status).toBe(200);
    const keys = res.body.networks.map((n: { key: string }) => n.key);
    expect(keys).toContain("base");
    expect(keys).toContain("arbitrum");
  });

  it("GET /api/settings returns current settings", async () => {
    const res = await request(handles.app).get("/api/settings");
    expect(res.status).toBe(200);
    expect(res.body).toHaveProperty("loanAmountUsd");
  });

  it("PATCH /api/settings updates a value and takes effect immediately", async () => {
    const res = await request(handles.app)
      .patch("/api/settings")
      .send({ loanAmountUsd: 123_456 });
    expect(res.status).toBe(200);
    expect(res.body.loanAmountUsd).toBe(123_456);

    const check = await request(handles.app).get("/api/settings");
    expect(check.body.loanAmountUsd).toBe(123_456);
  });

  it("PATCH /api/settings rejects invalid input with 400", async () => {
    const res = await request(handles.app).patch("/api/settings").send({ slippageBps: -10 });
    expect(res.status).toBe(400);
    expect(res.body.error).toBe("validation_error");
  });

  it("GET /api/opportunities returns detected opportunities after a scan", async () => {
    await handles.engine.tick();
    const res = await request(handles.app).get("/api/opportunities");
    expect(res.status).toBe(200);
    expect(res.body.opportunities.length).toBeGreaterThan(0);
  });

  it("POST /api/execute/:id executes an opportunity", async () => {
    await handles.engine.tick();
    const list = await request(handles.app).get("/api/opportunities");
    const id = list.body.opportunities[0].id;

    const res = await request(handles.app).post(`/api/execute/${id}`);
    expect(res.status).toBe(200);
    expect(res.body.opportunityId).toBe(id);
  });

  it("POST /api/execute/:id 400s for an unknown id", async () => {
    const res = await request(handles.app).post("/api/execute/does-not-exist");
    expect(res.status).toBe(400);
  });

  it("POST /api/engine/toggle flips the master switch", async () => {
    const res = await request(handles.app).post("/api/engine/toggle").send({ enabled: false });
    expect(res.status).toBe(200);
    expect(res.body.engineEnabled).toBe(false);
  });

  it("GET /api/latency returns the pipeline latency snapshot shape", async () => {
    const res = await request(handles.app).get("/api/latency");
    expect(res.status).toBe(200);
    expect(Array.isArray(res.body.components)).toBe(true);
    expect(res.body).toHaveProperty("endToEnd");
    expect(res.body).toHaveProperty("samples");
    // Simulated data has no ingestion anchor, so end-to-end is not yet measurable.
    expect(res.body.anchored).toBe(false);
  });

  it("GET /api/health/execution reports read-only readiness (unconfigured in paper)", async () => {
    const res = await request(handles.app).get("/api/health/execution");
    expect(res.status).toBe(200);
    // No RPC configured in the test env → configured:false, and crucially it never
    // attempts a broadcast or a signer (pure read-only probe).
    expect(res.body.configured).toBe(false);
    expect(res.body.healthy).toBe(false);
    expect(Array.isArray(res.body.stages)).toBe(true);
  });
});
