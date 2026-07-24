/**
 * Static registry of supported Layer-2 networks and the DEX venues we scan on
 * each. This is the source of truth surfaced at `GET /api/networks` and used by
 * the frontend to render network/DEX pickers.
 */
export interface DexInfo {
  key: string;
  name: string;
  /** Flash-loan / swap fee charged by the venue, in basis points. */
  feeBps: number;
}

export interface NetworkInfo {
  key: string;
  name: string;
  chainId: number;
  nativeCurrency: string;
  explorer: string;
  /** Typical L2 gas cost for a flash-loan arbitrage bundle, in USD. */
  typicalGasUsd: number;
  dexes: DexInfo[];
  enabledByDefault: boolean;
}

export const NETWORKS: NetworkInfo[] = [
  {
    key: "base",
    name: "Base",
    chainId: 8453,
    nativeCurrency: "ETH",
    explorer: "https://basescan.org",
    typicalGasUsd: 0.04,
    enabledByDefault: true,
    dexes: [
      { key: "aerodrome", name: "Aerodrome", feeBps: 5 },
      { key: "uniswap-v3", name: "Uniswap v3", feeBps: 5 },
      { key: "sushiswap", name: "SushiSwap", feeBps: 30 },
      { key: "pancakeswap", name: "PancakeSwap", feeBps: 25 },
    ],
  },
  {
    key: "arbitrum",
    name: "Arbitrum One",
    chainId: 42161,
    nativeCurrency: "ETH",
    explorer: "https://arbiscan.io",
    typicalGasUsd: 0.08,
    enabledByDefault: true,
    dexes: [
      { key: "camelot", name: "Camelot", feeBps: 30 },
      { key: "uniswap-v3", name: "Uniswap v3", feeBps: 5 },
      { key: "sushiswap", name: "SushiSwap", feeBps: 30 },
      { key: "ramses", name: "Ramses", feeBps: 5 },
    ],
  },
  {
    key: "optimism",
    name: "OP Mainnet",
    chainId: 10,
    nativeCurrency: "ETH",
    explorer: "https://optimistic.etherscan.io",
    typicalGasUsd: 0.05,
    enabledByDefault: false,
    dexes: [
      { key: "velodrome", name: "Velodrome", feeBps: 5 },
      { key: "uniswap-v3", name: "Uniswap v3", feeBps: 5 },
      { key: "sushiswap", name: "SushiSwap", feeBps: 30 },
    ],
  },
  {
    key: "polygon",
    name: "Polygon PoS",
    chainId: 137,
    nativeCurrency: "MATIC",
    explorer: "https://polygonscan.com",
    typicalGasUsd: 0.02,
    enabledByDefault: false,
    dexes: [
      { key: "quickswap", name: "QuickSwap", feeBps: 30 },
      { key: "uniswap-v3", name: "Uniswap v3", feeBps: 5 },
      { key: "sushiswap", name: "SushiSwap", feeBps: 30 },
    ],
  },
  {
    // Unichain and Ink are fed by the Rust ingestion layer (chain set:
    // Arbitrum, Base, Optimism, Unichain, Ink). They are registered here so
    // opportunities the detection engine reports on them map to a known
    // network instead of "unknown".
    key: "unichain",
    name: "Unichain",
    chainId: 130,
    nativeCurrency: "ETH",
    explorer: "https://uniscan.xyz",
    typicalGasUsd: 0.03,
    enabledByDefault: false,
    dexes: [
      { key: "uniswap-v4", name: "Uniswap v4", feeBps: 5 },
      { key: "uniswap-v3", name: "Uniswap v3", feeBps: 5 },
    ],
  },
  {
    key: "ink",
    name: "Ink",
    chainId: 57073,
    nativeCurrency: "ETH",
    explorer: "https://explorer.inkonchain.com",
    typicalGasUsd: 0.02,
    enabledByDefault: false,
    dexes: [
      { key: "uniswap-v3", name: "Uniswap v3", feeBps: 5 },
      { key: "sushiswap", name: "SushiSwap", feeBps: 30 },
    ],
  },
];

export const NETWORKS_BY_KEY: Record<string, NetworkInfo> = Object.fromEntries(
  NETWORKS.map((n) => [n.key, n]),
);

/** Reverse lookup by numeric chain id — used to map engine opportunities
 *  (which carry `chain_id`) onto a dashboard network. */
export const NETWORKS_BY_CHAIN_ID: Record<number, NetworkInfo> = Object.fromEntries(
  NETWORKS.map((n) => [n.chainId, n]),
);

/** Flash-loan providers and their fee in basis points of the borrowed amount. */
export const FLASH_LOAN_PROVIDERS: Record<string, { name: string; feeBps: number }> = {
  "aave-v3": { name: "Aave v3", feeBps: 5 },
  "balancer-v2": { name: "Balancer v2", feeBps: 0 },
  "uniswap-v3": { name: "Uniswap v3 flash", feeBps: 0 },
};
