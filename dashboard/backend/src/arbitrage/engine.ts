import { EventEmitter } from "node:events";
import { performance } from "node:perf_hooks";
import type { SettingsStore } from "../settings/store";
import type { Settings } from "../settings/schema";
import { createLogger } from "../util/logger";
import type { OpportunityProvider } from "./providers/provider";
import type { Executor } from "./executor";
import type { LatencyMonitor } from "./latency";
import type { ArbitrageOpportunity, EngineStats, ExecutionResult } from "./types";

const log = createLogger("engine");
const MAX_ACTIVE = 250;

export interface EngineEvents {
  opportunity: (opp: ArbitrageOpportunity) => void;
  "opportunity:remove": (id: string) => void;
  execution: (result: ExecutionResult) => void;
  stats: (stats: EngineStats) => void;
  alert: (alert: { level: "info" | "warn" | "error"; message: string }) => void;
}

/**
 * The scanning + execution loop. It is fully driven by the SettingsStore: the
 * scan cadence, profitability thresholds, risk limits, and auto-execution are
 * all read live on every tick, and a change to scanIntervalMs reschedules the
 * timer immediately. That is what "every control is wired to the backend and
 * takes effect in real time" means concretely.
 */
export class ArbitrageEngine extends EventEmitter {
  private timer: NodeJS.Timeout | null = null;
  private active = new Map<string, ArbitrageOpportunity>();
  private inFlight = 0;
  private lastExecByNetwork = new Map<string, number>();
  private startedAt = 0;
  private currentDay = dayKey();
  private unsubSettings: (() => void) | null = null;
  private scanning = false;

  private stats = {
    scans: 0,
    opportunitiesDetected: 0,
    executed: 0,
    filled: 0,
    reverted: 0,
    realizedPnlUsd: 0,
    dailyPnlUsd: 0,
    bestNetProfitUsd: 0,
    lastScanTs: null as number | null,
  };

  constructor(
    private readonly store: SettingsStore,
    private readonly provider: OpportunityProvider,
    private readonly executors: { paper: Executor; live: Executor },
    private readonly latency?: LatencyMonitor,
  ) {
    super();
  }

  async start() {
    this.startedAt = Date.now();
    await this.provider.start();
    this.schedule();
    // Re-arm the timer whenever the scan cadence changes.
    this.unsubSettings = this.store.onChange(({ changed }) => {
      if (changed.includes("scanIntervalMs")) this.schedule();
    });
    log.info(`engine started (provider=${this.provider.kind})`);
  }

  async stop() {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    this.unsubSettings?.();
    this.unsubSettings = null;
    await this.provider.stop();
    log.info("engine stopped");
  }

  private schedule() {
    if (this.timer) clearInterval(this.timer);
    const interval = this.store.get().scanIntervalMs;
    this.timer = setInterval(() => void this.tick(), interval);
    if (typeof this.timer.unref === "function") this.timer.unref();
  }

  /** Run a single scan/execute cycle. Exposed for tests. */
  async tick(): Promise<void> {
    if (this.scanning) return; // avoid overlap on slow providers
    this.scanning = true;
    try {
      const settings = this.store.get();
      this.rolloverDayIfNeeded();
      this.prune();

      if (!settings.engineEnabled) return;

      // Time the dashboard scan stage: drain the provider's freshest batch and
      // apply the live profit/position filters. The per-opportunity fan-out to
      // clients (emit → hub) is measured separately, so this stage excludes it.
      const scanStart = performance.now();
      const candidates = await this.provider.scan(settings);
      this.stats.scans += 1;
      const scanTs = Date.now();
      this.stats.lastScanTs = scanTs;
      const qualifying = candidates.filter(
        (opp) => opp.expiresAt > scanTs && this.qualifies(opp, settings),
      );
      this.latency?.record("dashboard", "scan", performance.now() - scanStart);

      for (const opp of qualifying) this.addOpportunity(opp);

      if (settings.autoExecute) await this.autoExecute(settings);
      this.emitStats();
    } catch (err) {
      log.error("scan tick failed", err);
      this.emit("alert", { level: "error", message: `scan failed: ${String(err)}` });
    } finally {
      this.scanning = false;
    }
  }

  private qualifies(opp: ArbitrageOpportunity, s: Settings): boolean {
    return (
      opp.netProfitUsd >= s.minProfitUsd &&
      opp.profitBps >= s.minProfitBps &&
      opp.amountInUsd <= s.maxPositionUsd
    );
  }

  private addOpportunity(opp: ArbitrageOpportunity) {
    this.active.set(opp.id, opp);
    this.stats.opportunitiesDetected += 1;
    if (opp.netProfitUsd > this.stats.bestNetProfitUsd) {
      this.stats.bestNetProfitUsd = opp.netProfitUsd;
    }
    this.emit("opportunity", opp);
    if (this.active.size > MAX_ACTIVE) {
      // Drop the oldest to bound memory.
      const oldest = [...this.active.values()].sort((a, b) => a.ts - b.ts)[0];
      if (oldest) this.removeOpportunity(oldest.id);
    }
  }

  private removeOpportunity(id: string) {
    if (this.active.delete(id)) this.emit("opportunity:remove", id);
  }

  private prune() {
    const now = Date.now();
    for (const [id, opp] of this.active) {
      if (opp.expiresAt <= now && opp.status !== "executing") this.removeOpportunity(id);
    }
  }

  private async autoExecute(settings: Settings) {
    if (this.stats.dailyPnlUsd <= -settings.maxDailyLossUsd) {
      this.emit("alert", {
        level: "warn",
        message: `daily loss limit reached ($${settings.maxDailyLossUsd}); auto-execution paused`,
      });
      return;
    }

    const ready = [...this.active.values()]
      .filter((o) => o.status === "new")
      .sort((a, b) => b.netProfitUsd - a.netProfitUsd);

    for (const opp of ready) {
      if (this.inFlight >= settings.maxConcurrentTrades) break;
      const last = this.lastExecByNetwork.get(opp.network) ?? 0;
      if (Date.now() - last < settings.cooldownMs) continue;
      // Fire-and-forget, but never leak an unhandled rejection: a throwing
      // executor (the gated LiveExecutor always refuses) is fully handled inside
      // executeOpportunity (status reset + alert); swallow here so live+auto mode
      // stays stable and the safety gate simply produces warnings, not crashes.
      void this.executeOpportunity(opp, settings).catch(() => {
        /* already handled in executeOpportunity */
      });
    }
  }

  /** Execute a single opportunity now (used by auto-exec and the manual route). */
  async executeOpportunity(
    oppOrId: ArbitrageOpportunity | string,
    settings: Settings = this.store.get(),
  ): Promise<ExecutionResult> {
    const opp = typeof oppOrId === "string" ? this.active.get(oppOrId) : oppOrId;
    if (!opp) throw new Error("opportunity not found or expired");

    const priorStatus = opp.status;
    opp.status = "executing";
    this.inFlight += 1;
    this.lastExecByNetwork.set(opp.network, Date.now());
    try {
      const executor = this.executors[settings.executionMode];
      const result = await executor.execute(opp, settings);
      this.applyResult(result);
      opp.status = result.status;
      this.emit("execution", result);
      this.removeOpportunity(opp.id);
      this.emitStats();
      return result;
    } catch (err) {
      // A throwing executor (by design, the gated LiveExecutor refuses to
      // broadcast) must not leave the opportunity wedged in "executing" —
      // prune() intentionally skips that status, so it would linger forever.
      // Restore the prior status so it can expire/prune normally, and surface
      // the reason to operators as an alert (also what the manual
      // POST /api/execute/:id path relies on to explain a refusal in the UI).
      if (this.active.has(opp.id)) opp.status = priorStatus;
      this.emit("alert", {
        level: "warn",
        message: `execution refused: ${(err as Error).message}`,
      });
      throw err;
    } finally {
      this.inFlight -= 1;
    }
  }

  private applyResult(result: ExecutionResult) {
    this.stats.executed += 1;
    if (result.status === "filled") this.stats.filled += 1;
    else this.stats.reverted += 1;
    this.stats.realizedPnlUsd += result.realizedProfitUsd;
    this.stats.dailyPnlUsd += result.realizedProfitUsd;
  }

  private rolloverDayIfNeeded() {
    const today = dayKey();
    if (today !== this.currentDay) {
      this.currentDay = today;
      this.stats.dailyPnlUsd = 0;
    }
  }

  getOpportunities(): ArbitrageOpportunity[] {
    return [...this.active.values()].sort((a, b) => b.netProfitUsd - a.netProfitUsd);
  }

  getStats(): EngineStats {
    const s = this.store.get();
    return {
      running: this.timer !== null && s.engineEnabled,
      dataSource: this.provider.kind,
      executionMode: s.executionMode,
      opportunitiesActive: this.active.size,
      uptimeMs: this.startedAt ? Date.now() - this.startedAt : 0,
      ...this.stats,
    };
  }

  private emitStats() {
    this.emit("stats", this.getStats());
  }
}

function dayKey(): string {
  return new Date().toISOString().slice(0, 10);
}
