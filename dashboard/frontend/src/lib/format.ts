/** Presentation helpers — all pure, all unit-tested. */

export function formatUsd(n: number, opts: { sign?: boolean; compact?: boolean } = {}): string {
  const { sign = false, compact = false } = opts;
  const abs = Math.abs(n);
  const decimals = abs === 0 ? 2 : abs >= 1000 ? 0 : abs >= 1 ? 2 : 4;
  const body = compact
    ? new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(abs)
    : new Intl.NumberFormat("en-US", {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
      }).format(abs);
  const prefix = n < 0 ? "-" : sign ? "+" : "";
  return `${prefix}$${body}`;
}

/**
 * Render a profit/amount magnitude honestly. When `isUsd` (the default) it is a
 * true dollar figure and formats with a `$`. When `isUsd` is false the value is
 * in numeraire base units — NOT dollars (no price is known to convert it) — so it
 * renders as a plain number suffixed with the token `symbol` and never a `$`.
 */
export function formatAmount(
  value: number,
  opts: { isUsd?: boolean; symbol?: string; sign?: boolean; compact?: boolean } = {},
): string {
  const { isUsd = true, symbol, sign = false, compact = false } = opts;
  if (isUsd) return formatUsd(value, { sign, compact });
  const abs = Math.abs(value);
  const decimals = abs === 0 ? 2 : abs >= 1000 ? 2 : abs >= 1 ? 4 : 6;
  const body = compact
    ? new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 2 }).format(abs)
    : new Intl.NumberFormat("en-US", {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
      }).format(abs);
  const prefix = value < 0 ? "-" : sign ? "+" : "";
  return symbol ? `${prefix}${body} ${symbol}` : `${prefix}${body}`;
}

/**
 * Honest data-source badge. Both real feeds — direct on-chain quoting (`live`)
 * and the Rust ingestion feed (`external`) — read as real data; only the built-in
 * `simulated` demo stream reads as SIM.
 */
export function dataSourceBadge(dataSource: string): { label: string; real: boolean } {
  const real = dataSource === "live" || dataSource === "external";
  return { label: real ? "LIVE DATA" : "SIM DATA", real };
}

export function formatNumber(n: number, decimals = 0): string {
  return new Intl.NumberFormat("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(n);
}

export function formatBps(bps: number): string {
  return `${bps >= 0 ? "" : "-"}${Math.abs(bps).toFixed(1)} bps`;
}

export function formatPct(fraction: number, decimals = 0): string {
  return `${(fraction * 100).toFixed(decimals)}%`;
}

export function shortAddress(addr?: string): string {
  if (!addr) return "";
  return `${addr.slice(0, 6)}…${addr.slice(-4)}`;
}

export function timeAgo(ts: number, now: number = Date.now()): string {
  const s = Math.max(0, Math.floor((now - ts) / 1000));
  if (s < 1) return "now";
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  return `${h}h ago`;
}

export function formatDuration(ms: number): string {
  const s = Math.floor(ms / 1000);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${sec}s`;
  return `${sec}s`;
}
