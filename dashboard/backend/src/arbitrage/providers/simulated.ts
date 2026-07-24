import { randomUUID } from "node:crypto";
import type { Settings } from "../../settings/schema";
import type { ArbitrageOpportunity, RouteLeg } from "../types";
import { NETWORKS_BY_KEY, FLASH_LOAN_PROVIDERS } from "../networks";
import type { OpportunityProvider } from "./provider";

/** Approximate USD reference prices used only to make simulated routes realistic. */
const REFERENCE_PRICE_USD: Record<string, number> = {
  USDC: 1,
  USDT: 1,
  DAI: 1,
  WETH: 3200,
  ETH: 3200,
  WBTC: 64000,
  ARB: 0.9,
  OP: 2.1,
  MATIC: 0.7,
  AERO: 1.2,
};

function gaussian(): number {
  // Box–Muller transform for a roughly normal spread distribution.
  let u = 0;
  let v = 0;
  while (u === 0) u = Math.random();
  while (v === 0) v = Math.random();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

function pick<T>(arr: T[]): T {
  return arr[Math.floor(Math.random() * arr.length)]!;
}

/**
 * Generates realistic-looking arbitrage opportunities without touching the
 * network. Spreads follow a normal distribution with occasional spikes, so the
 * stream feels like a real market: mostly noise, periodically a live edge.
 */
export class SimulatedProvider implements OpportunityProvider {
  readonly kind = "simulated" as const;

  start() {
    /* no-op */
  }

  stop() {
    /* no-op */
  }

  async scan(settings: Settings): Promise<ArbitrageOpportunity[]> {
    const now = Date.now();
    const out: ArbitrageOpportunity[] = [];

    // A handful of candidate checks per scan; most yield nothing profitable.
    const candidates = 4 + Math.floor(Math.random() * 4);
    for (let i = 0; i < candidates; i++) {
      const opp = this.tryBuildOpportunity(settings, now);
      if (opp) out.push(opp);
    }
    return out;
  }

  private tryBuildOpportunity(settings: Settings, now: number): ArbitrageOpportunity | null {
    const networkKey = pick(settings.networks);
    const network = NETWORKS_BY_KEY[networkKey];
    if (!network) return null;

    // DEX venues available on this network that the user has enabled.
    const venues = network.dexes.filter((d) => settings.dexes.includes(d.key));
    if (venues.length < 2) return null;

    const buyVenue = pick(venues);
    let sellVenue = pick(venues);
    if (sellVenue.key === buyVenue.key) return null;

    // Pick a traded token (the volatile asset) distinct from the base token.
    const tradable = settings.tokens.filter((t) => t !== settings.baseToken);
    if (tradable.length === 0) return null;
    const token = pick(tradable);

    // Raw spread in bps: centered near zero (noise), with periodic positive
    // spikes that represent genuine, fleeting cross-venue dislocations. Tuned so
    // the stream feels alive (a real edge every few seconds) while most
    // candidates remain unprofitable after fees — as in a real L2 market.
    const spike = Math.random() < 0.25 ? Math.abs(gaussian()) * 30 : 0;
    const spreadBps = gaussian() * 6 + spike;
    if (spreadBps <= 0) return null;

    const amountInUsd = settings.loanAmountUsd;
    const grossProfitUsd = (amountInUsd * spreadBps) / 10_000;

    const flpFeeBps = FLASH_LOAN_PROVIDERS[settings.flashLoanProvider]?.feeBps ?? 0;
    const flashLoanFeeUsd = (amountInUsd * flpFeeBps) / 10_000;

    // DEX swap fees (both legs) eat into gross.
    const swapFeeUsd = (amountInUsd * (buyVenue.feeBps + sellVenue.feeBps)) / 10_000;

    // L2 gas jitter around the network's typical cost.
    const gasCostUsd = network.typicalGasUsd * (0.6 + Math.random() * 1.2);

    const netProfitUsd = grossProfitUsd - flashLoanFeeUsd - swapFeeUsd - gasCostUsd;
    const profitBps = (netProfitUsd / amountInUsd) * 10_000;

    const refPrice = REFERENCE_PRICE_USD[token] ?? 100;
    const buyPrice = refPrice;
    const sellPrice = refPrice * (1 + spreadBps / 10_000);

    const route: RouteLeg[] = [
      {
        dex: buyVenue.key,
        tokenIn: settings.baseToken,
        tokenOut: token,
        price: 1 / buyPrice,
        poolFeeBps: buyVenue.feeBps,
      },
      {
        dex: sellVenue.key,
        tokenIn: token,
        tokenOut: settings.baseToken,
        price: sellPrice,
        poolFeeBps: sellVenue.feeBps,
      },
    ];

    // Confidence falls as the edge thins relative to gas.
    const confidence = Math.max(
      0.05,
      Math.min(0.98, netProfitUsd / (netProfitUsd + gasCostUsd * 4 + 1)),
    );

    return {
      id: randomUUID(),
      ts: now,
      network: network.key,
      chainId: network.chainId,
      tokenIn: settings.baseToken,
      route,
      amountInUsd,
      grossProfitUsd,
      flashLoanFeeUsd: flashLoanFeeUsd + swapFeeUsd,
      gasCostUsd,
      netProfitUsd,
      profitBps,
      spreadBps,
      confidence,
      status: "new",
      expiresAt: now + 6000 + Math.floor(Math.random() * 6000),
    };
  }
}
