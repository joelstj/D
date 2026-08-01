import { createPublicClient, http, type Chain, type PublicClient } from "viem";
import { arbitrum, base, ink, optimism, polygon, unichain } from "viem/chains";
import type { ChainProbe } from "./service";

const VIEM_CHAINS: Record<string, Chain> = { base, arbitrum, optimism, polygon, unichain, ink };

const PREMIUM_ABI = [
  {
    type: "function",
    name: "aavePremiumBps",
    stateMutability: "view",
    inputs: [],
    outputs: [{ type: "uint256" }],
  },
] as const;

/**
 * Production {@link ChainProbe}: lazily-built, read-only viem public clients, one
 * per configured chain. `getCodeSize` reads deployed bytecode; `premiumBps`
 * staticCalls a cheap view. No signer is ever constructed — this can only read.
 */
export class ViemChainProbe implements ChainProbe {
  private readonly clients = new Map<string, PublicClient>();

  constructor(private readonly rpcUrls: Record<string, string | undefined>) {}

  private client(chainKey: string): PublicClient {
    const cached = this.clients.get(chainKey);
    if (cached) return cached;
    const url = this.rpcUrls[chainKey];
    const chain = VIEM_CHAINS[chainKey];
    if (!url) throw new Error(`no RPC configured for ${chainKey}`);
    if (!chain) throw new Error(`unsupported chain ${chainKey}`);
    const c = createPublicClient({ chain, transport: http(url) }) as PublicClient;
    this.clients.set(chainKey, c);
    return c;
  }

  async getCodeSize(chainKey: string, address: string): Promise<number> {
    const code = await this.client(chainKey).getCode({ address: address as `0x${string}` });
    return code ? (code.length - 2) / 2 : 0;
  }

  async premiumBps(chainKey: string, address: string): Promise<number> {
    const v = (await this.client(chainKey).readContract({
      address: address as `0x${string}`,
      abi: PREMIUM_ABI,
      functionName: "aavePremiumBps",
    })) as bigint;
    return Number(v);
  }
}
