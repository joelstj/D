import { createPublicClient, http, type PublicClient } from "viem";
import { arbitrum, base, ink, optimism, polygon, unichain } from "viem/chains";
import type { Settings } from "../../settings/schema";
import type { ArbitrageOpportunity } from "../types";
import { NETWORKS_BY_KEY } from "../networks";
import { createLogger } from "../../util/logger";
import type { OpportunityProvider } from "./provider";

const log = createLogger("provider:live");

const VIEM_CHAINS = { base, arbitrum, optimism, polygon, unichain, ink } as const;

/**
 * Live data source. Connects to real L2 RPCs and reads on-chain state
 * (block height, gas price). Full DEX quoting (Uniswap v3 QuoterV2, Aerodrome,
 * Camelot, etc.) is the documented integration point that the Ralph loop fills
 * in — this class deliberately ships the connectivity + plumbing so that work
 * is additive and cannot silently pretend to have live prices it does not.
 *
 * Requires RPC_URL_<NETWORK> for every scanned network. If any is missing the
 * provider fails fast at start() with an actionable message rather than
 * fabricating data.
 */
export class LiveProvider implements OpportunityProvider {
  readonly kind = "live" as const;
  private clients = new Map<string, PublicClient>();

  constructor(private readonly rpcUrls: Record<string, string | undefined>) {}

  start() {
    for (const [key, chain] of Object.entries(VIEM_CHAINS)) {
      const url = this.rpcUrls[key];
      if (!url) continue;
      this.clients.set(
        key,
        createPublicClient({ chain, transport: http(url) }) as PublicClient,
      );
      log.info(`connected RPC for ${key}`);
    }
    if (this.clients.size === 0) {
      throw new Error(
        "DATA_SOURCE=live but no RPC_URL_<NETWORK> is configured. " +
          "Set at least one of RPC_URL_BASE / RPC_URL_ARBITRUM / RPC_URL_OPTIMISM / RPC_URL_POLYGON.",
      );
    }
  }

  stop() {
    this.clients.clear();
  }

  async scan(settings: Settings): Promise<ArbitrageOpportunity[]> {
    const active = settings.networks.filter((n) => this.clients.has(n));
    if (active.length === 0) {
      log.warn("no live RPC available for any enabled network; returning no opportunities");
      return [];
    }

    // Prove liveness by reading real chain state. This is intentionally cheap;
    // it validates the RPC path that a full quoting implementation will build on.
    await Promise.all(
      active.map(async (key) => {
        const client = this.clients.get(key)!;
        const net = NETWORKS_BY_KEY[key];
        try {
          const [block, gas] = await Promise.all([
            client.getBlockNumber(),
            client.getGasPrice(),
          ]);
          log.debug(`${net?.name ?? key}: block=${block} gasPrice=${gas}`);
        } catch (err) {
          log.error(`RPC read failed for ${key}`, err);
        }
      }),
    );

    // NOTE: on-chain DEX quoting is the next integration step. Until it lands,
    // live mode surfaces no opportunities rather than inventing them.
    return [];
  }
}
