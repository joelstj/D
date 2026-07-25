import { performance } from "node:perf_hooks";
import { WebSocket } from "ws";
import type { Settings } from "../../settings/schema";
import type { ArbitrageOpportunity } from "../types";
import { createLogger } from "../../util/logger";
import type { LatencyMonitor, ComponentTiming } from "../latency";
import { PIPELINE_COMPONENT } from "../latency";
import type { OpportunityProvider } from "./provider";
import {
  mapEngineOpportunity,
  DEFAULT_TTL_MS,
  type EngineOpportunity,
} from "./engineMap";

/** The ingestion latency trace carried on an opportunities envelope. */
interface IngestionLatency extends ComponentTiming {
  origin_wall_ms?: number;
}
/** The parsed opportunities frame (only the fields we read). */
interface OpportunitiesFrame {
  kind?: string;
  latency?: IngestionLatency;
  payload?: { opportunities?: EngineOpportunity[]; timing?: ComponentTiming };
}

const log = createLogger("provider:external");

/** Minimal structural constructor so tests can inject a fake WebSocket. */
export interface WebSocketLike {
  on(event: string, cb: (...args: unknown[]) => void): void;
  close(): void;
}
export type WebSocketCtor = new (url: string) => WebSocketLike;

export interface ExternalProviderOptions {
  /** ms an unrefreshed opportunity stays buffered (default {@link DEFAULT_TTL_MS}). */
  ttlMs?: number;
  /** reconnect backoff after a dropped connection (default 1500 ms). */
  reconnectMs?: number;
  /** injectable WebSocket implementation (default the `ws` client). */
  WebSocketCtor?: WebSocketCtor;
  /** latency aggregator fed the ingestion/engine trace + this stage's parse/map. */
  latency?: LatencyMonitor;
}

/**
 * The **real-data** provider for the merged bot. It connects to the Rust
 * ingestion layer's output sink (`[output] sink = "ws"`, default
 * `ws://127.0.0.1:9001`) and consumes the versioned envelopes it broadcasts:
 *
 *     { schema_version, kind: "opportunities", chain_blocks, payload: DetectResponse }
 *
 * Each `kind: "opportunities"` frame carries the l2arb detection engine's ranked
 * output. We map every opportunity onto the dashboard shape (see
 * {@link mapEngineOpportunity}) and buffer it keyed by a block-stable id, so the
 * pull-based engine can drain the freshest batch on each `scan()`.
 *
 * Failure handling follows the "fail loud on bad data, degrade gracefully on
 * infra" rule: a malformed frame is logged and dropped (never crashes the
 * backend); a dropped socket triggers reconnect with backoff.
 */
export class ExternalProvider implements OpportunityProvider {
  readonly kind = "external" as const;

  private ws: WebSocketLike | null = null;
  private closed = false;
  private reconnectTimer: NodeJS.Timeout | null = null;

  /** Latest opportunity per stable id. */
  private buffer = new Map<string, ArbitrageOpportunity>();
  /** Ids received/updated since the last scan (drained each scan). */
  private dirty = new Set<string>();

  private readonly ttlMs: number;
  private readonly reconnectMs: number;
  private readonly WebSocketCtor: WebSocketCtor;
  private readonly latency: LatencyMonitor | null;

  // Lightweight observability (surfaced in logs / testable).
  private framesReceived = 0;
  private oppsMapped = 0;
  private oppsDropped = 0;
  private connected = false;

  constructor(
    private readonly feedUrl: string,
    opts: ExternalProviderOptions = {},
  ) {
    this.ttlMs = opts.ttlMs ?? DEFAULT_TTL_MS;
    this.reconnectMs = opts.reconnectMs ?? 1500;
    this.WebSocketCtor = opts.WebSocketCtor ?? (WebSocket as unknown as WebSocketCtor);
    this.latency = opts.latency ?? null;
  }

  start(): void {
    this.closed = false;
    this.connect();
    log.info(`external provider connecting to ingestion feed ${this.feedUrl}`);
  }

  stop(): void {
    this.closed = true;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    try {
      this.ws?.close();
    } catch {
      /* ignore */
    }
    this.ws = null;
    this.buffer.clear();
    this.dirty.clear();
  }

  private connect(): void {
    if (this.closed) return;
    let ws: WebSocketLike;
    try {
      ws = new this.WebSocketCtor(this.feedUrl);
    } catch (err) {
      log.error(`failed to open feed ${this.feedUrl}`, err);
      this.scheduleReconnect();
      return;
    }
    this.ws = ws;

    ws.on("open", () => {
      this.connected = true;
      log.info(`connected to ingestion feed ${this.feedUrl}`);
    });
    ws.on("message", (data: unknown) => this.handleFrame(data));
    ws.on("error", (err: unknown) => {
      log.warn(`feed error: ${String(err)}`);
    });
    ws.on("close", () => {
      this.connected = false;
      if (!this.closed) {
        log.warn(`feed closed; reconnecting in ${this.reconnectMs}ms`);
        this.scheduleReconnect();
      }
    });
  }

  private scheduleReconnect(): void {
    if (this.closed || this.reconnectTimer) return;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, this.reconnectMs);
    if (typeof this.reconnectTimer.unref === "function") this.reconnectTimer.unref();
  }

  /** Parse one envelope frame and buffer any opportunities it carries. Never throws. */
  private handleFrame(data: unknown): void {
    this.framesReceived += 1;
    const parseStart = performance.now();
    let env: OpportunitiesFrame;
    try {
      const text =
        typeof data === "string"
          ? data
          : data instanceof Buffer
            ? data.toString("utf8")
            : String(data);
      env = JSON.parse(text);
    } catch (err) {
      log.warn(`dropping unparseable frame: ${String(err)}`);
      return;
    }
    this.latency?.record("dashboard", "parse", performance.now() - parseStart);
    if (env.kind !== "opportunities") return; // ignore snapshot / other kinds
    const opps = env.payload?.opportunities;
    if (!Array.isArray(opps)) return;

    const now = Date.now();
    // Fold the relayed upstream traces (ingestion + engine) into the aggregator and
    // measure the cross-process gap from ingest origin to this receipt.
    const originWallMs = toFinite(env.latency?.origin_wall_ms);
    if (this.latency) {
      this.latency.recordComponent(env.latency);
      this.latency.recordComponent(env.payload?.timing);
      if (originWallMs > 0) {
        this.latency.record(PIPELINE_COMPONENT, "ingest_to_dashboard", now - originWallMs);
      }
    }

    const mapStart = performance.now();
    for (const raw of opps) {
      try {
        const opp = mapEngineOpportunity(raw, now, this.ttlMs, originWallMs || undefined);
        this.buffer.set(opp.id, opp);
        this.dirty.add(opp.id);
        this.oppsMapped += 1;
      } catch (err) {
        this.oppsDropped += 1;
        log.warn(`dropping malformed opportunity: ${String(err)}`);
      }
    }
    this.latency?.record("dashboard", "map", performance.now() - mapStart);
  }

  /**
   * Return the batch of opportunities received since the last scan. The engine
   * upserts them by id and applies its own profitability/expiry filters, so we
   * only hand over freshly-updated rows (drained here) and prune expired ones.
   */
  async scan(_settings: Settings): Promise<ArbitrageOpportunity[]> {
    const now = Date.now();
    // Prune anything that has aged out of the buffer.
    for (const [id, opp] of this.buffer) {
      if (opp.expiresAt <= now) {
        this.buffer.delete(id);
        this.dirty.delete(id);
      }
    }
    const batch: ArbitrageOpportunity[] = [];
    for (const id of this.dirty) {
      const opp = this.buffer.get(id);
      if (opp) batch.push(opp);
    }
    this.dirty.clear();
    return batch;
  }

  /** Snapshot of connection/mapping counters (for logs, /api/stats, and tests). */
  getState(): {
    connected: boolean;
    framesReceived: number;
    oppsMapped: number;
    oppsDropped: number;
    buffered: number;
  } {
    return {
      connected: this.connected,
      framesReceived: this.framesReceived,
      oppsMapped: this.oppsMapped,
      oppsDropped: this.oppsDropped,
      buffered: this.buffer.size,
    };
  }
}

/** Coerce an optional numeric field to a finite number (0 when absent/invalid). */
function toFinite(x: unknown): number {
  const n = typeof x === "number" ? x : Number(x);
  return Number.isFinite(n) ? n : 0;
}
