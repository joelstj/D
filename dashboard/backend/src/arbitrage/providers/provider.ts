import type { Settings } from "../../settings/schema";
import type { ArbitrageOpportunity } from "../types";

/**
 * An OpportunityProvider is the pluggable data source for the engine. The
 * simulated provider works with zero configuration; the live provider polls
 * real DEX price feeds over configured RPCs. Both expose the exact same
 * interface, so switching between paper research and live data is a one-line
 * config change (DATA_SOURCE=simulated|live) with no engine changes.
 */
export interface OpportunityProvider {
  readonly kind: "simulated" | "live" | "external";
  /** Called once before the first scan. */
  start(): Promise<void> | void;
  /** Called on shutdown. */
  stop(): Promise<void> | void;
  /**
   * Produce the current batch of candidate opportunities given live settings.
   * The engine applies profitability/risk filters afterward, so a provider may
   * return raw candidates including marginal ones.
   */
  scan(settings: Settings): Promise<ArbitrageOpportunity[]>;
}
