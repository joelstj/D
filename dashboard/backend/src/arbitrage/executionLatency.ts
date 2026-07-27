/**
 * On-chain **execution-readiness** latency probe — the second, separate health
 * check required by the root `CLAUDE.md`: distinct from the opportunity-display
 * pipeline, it measures how responsive the execution path's chain access is.
 *
 * ## Safety (invariant 3 — binding)
 * This probe is **strictly read-only**. It times `eth_blockNumber` / `eth_gasPrice`
 * round-trips and, only when an operator supplies a deployed executor address, a
 * `staticCall` (`eth_call`) of the contract's cheap `aavePremiumBps()` view. It
 * **never** constructs a signer, never sends a transaction, never calls
 * `executeArbitrage` as a write, and never touches `LiveExecutor` (which still
 * refuses to broadcast). Execution stays paper-by-default and human-gated; this is
 * pure observability of the read path an eventual human-authorised signer builds on.
 *
 * The chain surface is injected (`ChainReader`) so tests are deterministic and
 * offline; in production a lazily-built viem `PublicClient` supplies it. With no
 * RPC configured (the paper-mode default) the probe reports `configured:false`
 * cleanly rather than inventing numbers.
 */
import { createPublicClient, http, type PublicClient } from "viem";
import { arbitrum, base, ink, optimism, polygon, unichain } from "viem/chains";
import type { Chain } from "viem";
import { createLogger } from "../util/logger";

const log = createLogger("execution-latency");

const VIEM_CHAINS: Record<string, Chain> = { base, arbitrum, optimism, polygon, unichain, ink };
/** Chain preference when none is pinned: the executor's canonical home first. */
const CHAIN_PREFERENCE = ["arbitrum", "base", "optimism", "polygon", "unichain", "ink"];

/** Minimal `aavePremiumBps()` view ABI — the cheapest contract-level probe. */
const PREMIUM_ABI = [
  {
    type: "function",
    name: "aavePremiumBps",
    stateMutability: "view",
    inputs: [],
    outputs: [{ type: "uint256" }],
  },
] as const;

/** The read-only chain surface the probe needs (injectable for tests). */
export interface ChainReader {
  getBlockNumber(): Promise<bigint>;
  getGasPrice(): Promise<bigint>;
  readContract(args: {
    address: `0x${string}`;
    abi: typeof PREMIUM_ABI;
    functionName: "aavePremiumBps";
  }): Promise<bigint>;
}

/** One execution-readiness measurement. */
export interface ExecutionLatencySample {
  /** Whether an RPC endpoint is configured at all (false → paper-mode default). */
  configured: boolean;
  /** Whether the last probe completed without error. */
  healthy: boolean;
  /** Chain key the probe ran against (`null` when unconfigured). */
  chain: string | null;
  /** Latest block height seen (`null` on failure/unconfigured). */
  blockNumber: number | null;
  /** Gas price in gwei (`null` on failure/unconfigured). */
  gasPriceGwei: number | null;
  /** Per-read latency stages (ms): `rpc_block`, `rpc_gas`, optional `contract_view`. */
  stages: { stage: string; ms: number }[];
  /** True when a deployed executor address was supplied and its view was probed. */
  contractProbed: boolean;
  /** Error message when `healthy` is false. */
  error: string | null;
  /** Wall-clock ms the sample was taken. */
  checkedAt: number;
}

export interface ExecutionLatencyOptions {
  rpcUrls: Record<string, string | undefined>;
  /** Optional deployed FlashLoanArbitrage address to `staticCall` a view on. */
  executorAddress?: string;
  /** Pin the probe chain; otherwise the first configured of {@link CHAIN_PREFERENCE}. */
  chain?: string;
  /** Injected read-only chain surface (tests bypass viem entirely). */
  reader?: ChainReader;
  /** Injected monotonic clock (ms); default `performance.now`. */
  now?: () => number;
  /** Min interval between real probes; repeat calls inside it return the cache. */
  cacheMs?: number;
}

function firstConfiguredChain(
  rpcUrls: Record<string, string | undefined>,
  pinned?: string,
): string | null {
  if (pinned && rpcUrls[pinned]) return pinned;
  for (const key of CHAIN_PREFERENCE) if (rpcUrls[key]) return key;
  return null;
}

export class ExecutionLatencyProbe {
  private readonly now: () => number;
  private readonly cacheMs: number;
  private readonly chainKey: string | null;
  private reader: ChainReader | null;
  private client: PublicClient | null = null;
  private last: ExecutionLatencySample | null = null;
  private lastAt = -Infinity;

  constructor(private readonly opts: ExecutionLatencyOptions) {
    this.now = opts.now ?? (() => performance.now());
    this.cacheMs = opts.cacheMs ?? 2000;
    this.chainKey = firstConfiguredChain(opts.rpcUrls, opts.chain);
    this.reader = opts.reader ?? null;
  }

  /** Latest readiness sample, re-probing at most once per `cacheMs`. */
  async get(): Promise<ExecutionLatencySample> {
    const wall = Date.now();
    if (this.last && wall - this.lastAt < this.cacheMs) return this.last;
    this.last = await this.probeOnce();
    this.lastAt = wall;
    return this.last;
  }

  /** Run a single read-only probe. Never throws; failures surface as `healthy:false`. */
  async probeOnce(): Promise<ExecutionLatencySample> {
    const base: ExecutionLatencySample = {
      configured: this.chainKey !== null,
      healthy: false,
      chain: this.chainKey,
      blockNumber: null,
      gasPriceGwei: null,
      stages: [],
      contractProbed: false,
      error: null,
      checkedAt: Date.now(),
    };
    if (this.chainKey === null) {
      // Paper-mode default: no RPC configured, nothing to measure. Not an error.
      return { ...base, error: "no RPC configured (set RPC_URL_<NETWORK> to enable)" };
    }

    let reader: ChainReader;
    try {
      reader = this.resolveReader();
    } catch (err) {
      return { ...base, error: `client init failed: ${String(err)}` };
    }

    const stages: { stage: string; ms: number }[] = [];
    try {
      const t0 = this.now();
      const block = await reader.getBlockNumber();
      stages.push({ stage: "rpc_block", ms: round(this.now() - t0) });

      const t1 = this.now();
      const gas = await reader.getGasPrice();
      stages.push({ stage: "rpc_gas", ms: round(this.now() - t1) });

      let contractProbed = false;
      if (this.opts.executorAddress) {
        const t2 = this.now();
        // Read-only staticCall of a cheap view — a revert here would mean the
        // address is wrong, never that a trade was attempted.
        await reader.readContract({
          address: this.opts.executorAddress as `0x${string}`,
          abi: PREMIUM_ABI,
          functionName: "aavePremiumBps",
        });
        stages.push({ stage: "contract_view", ms: round(this.now() - t2) });
        contractProbed = true;
      }

      return {
        ...base,
        healthy: true,
        blockNumber: Number(block),
        gasPriceGwei: round(Number(gas) / 1e9),
        stages,
        contractProbed,
        checkedAt: Date.now(),
      };
    } catch (err) {
      log.warn(`execution probe failed on ${this.chainKey}: ${String(err)}`);
      return { ...base, stages, error: String(err), checkedAt: Date.now() };
    }
  }

  private resolveReader(): ChainReader {
    if (this.reader) return this.reader;
    const url = this.opts.rpcUrls[this.chainKey!];
    const chain = VIEM_CHAINS[this.chainKey!];
    if (!url || !chain) throw new Error(`no RPC for chain ${this.chainKey}`);
    if (!this.client) {
      this.client = createPublicClient({ chain, transport: http(url) }) as PublicClient;
    }
    const client = this.client;
    // Adapt the viem client to the narrow read-only surface (keeps tests tiny).
    this.reader = {
      getBlockNumber: () => client.getBlockNumber(),
      getGasPrice: () => client.getGasPrice(),
      readContract: (args) =>
        client.readContract(args) as Promise<bigint>,
    };
    return this.reader;
  }
}

function round(x: number): number {
  return Math.round(x * 1000) / 1000;
}
