/** Display metadata for network keys. Colors come from the validated categorical
 * slots; the network name is always rendered alongside, so color is a secondary
 * cue and never the sole identifier. */
export const NETWORK_COLORS: Record<string, string> = {
  base: "var(--color-net-base)",
  arbitrum: "var(--color-net-arbitrum)",
  optimism: "var(--color-net-optimism)",
  polygon: "var(--color-net-polygon)",
};

export function networkColor(key: string): string {
  return NETWORK_COLORS[key] ?? "var(--color-ink-faint)";
}

export const DEX_LABELS: Record<string, string> = {
  "uniswap-v3": "Uniswap v3",
  aerodrome: "Aerodrome",
  camelot: "Camelot",
  sushiswap: "SushiSwap",
  pancakeswap: "PancakeSwap",
  ramses: "Ramses",
  velodrome: "Velodrome",
  quickswap: "QuickSwap",
};

export function dexLabel(key: string): string {
  return DEX_LABELS[key] ?? key;
}
