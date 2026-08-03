import { EventEmitter } from "node:events";
import { performance } from "node:perf_hooks";
import type { SettingsStore } from "../settings/store";
import type { Settings } from "../settings/schema";
import { createLogger } from "../util/logger";
import type { OpportunityProvider } from "./providers/provider";
import type { Executor } from "./executor";
import type { LatencyMonitor } from "./latency";
import type { ArbitrageOpportunity, EngineStats, ExecutionResult } from "./types";
import { NETWORKS } from "./networks";

const log = createLogger("engine");
const MAX_ACTIVE = 250;

/**
 * Every DEX **venue key** the dashboard models, across all networks (e.g.
 * `"uniswap-v3"`, `"aerodrome"`). The `dexes` Settings chips are venue keys, so
 * the venue filter in {@link ArbitrageEngine.qualifies} can only apply to a
 * route leg that is *labelled with a venue key we recognise*.
 *
 * The SimulatedProvider labels legs with venue keys. The real production feed
 * (ExternalProvider → the l2arb detection engine) does not: the engine prices by
 * pool address and never surfaces a venue brand, so `engineMap` labels a leg
 * with its (shortened) pool address. Fabricating a venue for such a leg would
 * violate the data-integrity invariant (root CLAUDE.md §2.1), so a venue-unlabelled
 * leg is simply not subject to the venue chip — the network/token/profit filters,
 * which are all derivable from real engine data, still apply to it.
 */
const KNOWN_VENUE_KEYS = new Set<string>(NETWORKS.flatMap((n) => n.dexes.map((d) => d.key)));

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
      // Push corrected stats immediately on a pause/resume rather than waiting
      // for the next scheduled tick — tick() itself also emits on the disabled
      // path (below) as a fallback, but a client shouldn't see a stale
      // "Running" state for up to a full scanIntervalMs after clicking pause.
      if (changed.includes("engineEnabled")) this.emitStats();
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

      if (!settings.engineEnabled) {
        // Without this, a client connected before the pause never receives a
        // corrected `running:false` — emitStats() is otherwise only reached
        // past this point, so the header's Play/Pause button kept showing
        // "Running" indefinitely after a pause (getStats() itself was always
        // correct on demand; nothing pushed the correction to existing
        // WS clients).
        this.emitStats();
        return;
      }

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

  /**
   * Applies every provider-agnostic filter: network/DEX/token universe and the
   * profit/position thresholds. Checked here — the one funnel every candidate
   * passes through regardless of provider (simulated, external, live) — so a
   * network or DEX chip toggled off in Settings actually stops opportunities on
   * it from qualifying, not just for providers that happen to consult it
   * themselves (previously only SimulatedProvider did; ExternalProvider, the
   * real production data source, silently ignored these controls entirely).
   *
   * Three honesty caveats, all grounded in what real engine data actually carries:
   *  - **Venue chip:** only legs labelled with a venue key we model (see
   *    {@link KNOWN_VENUE_KEYS}) are filtered by `s.dexes`. The engine prices by
   *    pool address and surfaces no venue brand, so an ExternalProvider leg is
   *    labelled with its pool address and is not subject to the venue chip —
   *    inventing a venue for it would be fabricated data. The network/token/profit
   *    filters, all derivable from real engine data, still apply. (Previously this
   *    line compared a pool address against venue keys, so *every* external
   *    opportunity was silently dropped — the whole live feed went dark.)
   *  - **Numeraire/base-token:** `opp.tokenIn` (the route's start/end token) is
   *    validated only via membership in `allowedTokens` (`s.tokens ∪
   *    {s.baseToken}`) below, not an exact match to `s.baseToken`. The engine
   *    closes each detected cycle in whichever configured hub token
   *    (`ingestion` `[[chains]].hubs`, typically WETH *and* USDC *and* USDT)
   *    actually produced the edge — that choice is the engine's, not the
   *    operator's. An exact-match check here silently dropped every real
   *    opportunity whose numeraire wasn't the one "Base asset" chip selected,
   *    even though the operator's own `tokens` list already allowed it.
   *  - **USD thresholds:** `minProfitUsd`/`maxPositionUsd` are dollar limits, so
   *    they apply only when the opportunity is USD-denominated. An opp is treated
   *    as USD unless it is *explicitly* flagged non-USD (`numeraireIsUsd === false`,
   *    which `engineMap` sets for a non-stablecoin numeraire whose `…Usd` fields are
   *    really numeraire base units). For a non-USD numeraire the unit-agnostic
   *    `minProfitBps` remains the gate; we never compare base units to dollars.
   */
  private qualifies(opp: ArbitrageOpportunity, s: Settings): boolean {
    if (!s.networks.includes(opp.network)) return false;
    const allowedTokens = new Set(s.tokens);
    allowedTokens.add(s.baseToken);
    for (const leg of opp.route) {
      // Venue chip applies only to legs carrying a venue key we recognise; an
      // unlabelled (pool-address) leg from the real engine feed is not filtered
      // here — see the KNOWN_VENUE_KEYS docstring.
      if (KNOWN_VENUE_KEYS.has(leg.dex) && !s.dexes.includes(leg.dex)) return false;
      if (!allowedTokens.has(leg.tokenIn) || !allowedTokens.has(leg.tokenOut)) return false;
    }
    if (opp.profitBps < s.minProfitBps) return false;
    // USD-magnitude gates: honest only when the figures are actually dollars.
    const usdDenominated = opp.numeraireIsUsd !== false;
    if (usdDenominated) {
      if (opp.netProfitUsd < s.minProfitUsd) return false;
      if (opp.amountInUsd > s.maxPositionUsd) return false;
    }
    return true;
  }

  /**
   * The reason `opp` cannot execute right now under the current risk limits, or
   * `null` if it's clear. This is the authoritative gate — called from
   * {@link executeOpportunity} itself, so a manual click or a direct
   * `POST /api/execute/:id` call is bound by the same "Risk & Limits" settings
   * as the auto-execute loop, not just whatever the loop happened to pre-filter.
   */
  private riskLimitBlock(opp: ArbitrageOpportunity, s: Settings): string | null {
    // Strict `<`: the limit means "halt once the day's loss *exceeds* this", so
    // `maxDailyLossUsd=0` (halt on the first real loss) does not trip at t0 when
    // `dailyPnlUsd` is still exactly 0 — a `<=` here blocked all execution from
    // the very first tick.
    if (this.stats.dailyPnlUsd < -s.maxDailyLossUsd) {
      return `daily loss limit reached ($${s.maxDailyLossUsd})`;
    }
    if (this.inFlight >= s.maxConcurrentTrades) {
      return `max concurrent trades reached (${s.maxConcurrentTrades})`;
    }
    const last = this.lastExecByNetwork.get(opp.network) ?? 0;
    const sinceLast = Date.now() - last;
    if (sinceLast < s.cooldownMs) {
      return `cooldown active for ${opp.network} (${s.cooldownMs - sinceLast}ms remaining)`;
    }
    return null;
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
    if (this.stats.dailyPnlUsd < -settings.maxDailyLossUsd) {
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
    if (!opp) {
      // Every other rejection in this function alerts before throwing (see
      // riskLimitBlock below and the catch block further down); this one
      // didn't, so clicking Execute on a row that expired or was already
      // executed a moment earlier (rows live only 6-12s by design) failed with
      // no toast, no error, nothing — the spinner just stopped.
      const message = "execution failed: opportunity not found or expired";
      this.emit("alert", { level: "warn", message });
      throw new Error(message);
    }

    const block = this.riskLimitBlock(opp, settings);
    if (block) {
      this.emit("alert", { level: "warn", message: `execution blocked: ${block}` });
      throw new Error(block);
    }

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
