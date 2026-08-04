/**
 * Faithful mapping from an l2arb **detection-engine** opportunity (the JSON the
 * Python engine emits, relayed by the Rust ingestion layer inside an
 * `Envelope { kind: "opportunities", payload: DetectResponse }`) onto the
 * dashboard's {@link ArbitrageOpportunity} shape.
 *
 * ## Data-integrity contract
 * The engine reports amounts as **decimal-string base units** denominated in the
 * opportunity's *numeraire* token, and already nets out gas + bridge costs. This
 * mapper only rescales by token decimals and relabels — it invents **no** price
 * data. Consequences worth understanding:
 *
 *  - When the numeraire is a USD stablecoin (USDC/USDT/DAI/…), the `…Usd` fields
 *    are true USD (1 base unit = 1e-6 or 1e-18 of a dollar-pegged token).
 *  - When the numeraire is **not** a USD stable (e.g. WETH), the `…Usd` fields
 *    carry magnitudes in **numeraire units**, not dollars — we do not fabricate
 *    an ETH price. `profitBps`, `score`/`confidence`, and the route are always
 *    numeraire-agnostic and exact.
 *
 * This keeps the merged product honest: every number is traceable to on-chain
 * state at the stamped block; nothing here is synthetic.
 */
import type { ArbitrageOpportunity, RouteLeg } from "../types";
import { NETWORKS_BY_CHAIN_ID } from "../networks";

/** How long a mapped opportunity stays "live" in the dashboard before it is
 *  pruned if the engine does not refresh it. Engine detections are per-block and
 *  short-lived, so this is intentionally small. */
export const DEFAULT_TTL_MS = 12_000;

/** Symbols we treat as ~1 USD so stablecoin-denominated figures read as dollars. */
const USD_STABLES = new Set([
  "USDC",
  "USDT",
  "DAI",
  "USDBC",
  "USDC.E",
  "USDCE",
  "FRAX",
  "LUSD",
  "USDE",
  "GHO",
  "SUSD",
]);

/** A token as it appears in the engine JSON. */
export interface EngineToken {
  chain_id: number;
  address: string;
  decimals: number;
  symbol: string;
}

export interface EngineLeg {
  pool: string;
  token_in: EngineToken;
  token_out: EngineToken;
  amount_in: string;
  amount_out: string;
}

export interface EngineBlock {
  chain_id: number;
  number: number;
  hash: string;
  timestamp: number;
}

/** The opportunity object produced by `l2arb.api.schema.opportunity_to_dict`. */
export interface EngineOpportunity {
  strategy: string;
  numeraire: EngineToken;
  input_amount: string;
  output_amount: string;
  gross_profit: string;
  gas_cost: string;
  bridge_cost: string;
  net_profit: string;
  profit_bps: number;
  expected_net: string;
  score: number;
  hops: number;
  chain_ids: number[];
  is_cross_chain: boolean;
  settle_seconds: number;
  verified: boolean;
  block: EngineBlock;
  risk: {
    success_probability: number;
    capture_ratio: number;
    frontrun_risk: number;
    notes: string[];
  };
  legs: EngineLeg[];
}

function num(x: unknown): number {
  const n = typeof x === "number" ? x : Number(x);
  return Number.isFinite(n) ? n : 0;
}

/** Scale a decimal-string base-unit amount to human units (base / 10**decimals).
 *  Display precision only; large values may lose low-order digits (acceptable). */
function scale(decStr: string, decimals: number): number {
  const v = num(decStr);
  if (v === 0) return 0;
  return v / 10 ** decimals;
}

function shortPool(addr: string): string {
  if (!addr || addr.length < 10) return addr || "pool";
  return `${addr.slice(0, 6)}…${addr.slice(-4)}`;
}

/**
 * Stable identity for an opportunity across blocks: the same cyclic route on the
 * same chain(s) updates in place in the dashboard (the engine re-emits it as
 * state moves) instead of flooding a new row every block.
 */
export function opportunityId(o: EngineOpportunity): string {
  const chains = [...o.chain_ids].sort((a, b) => a - b).join(",");
  const path = o.legs.map((l) => l.pool.toLowerCase()).join(">");
  return `l2arb:${o.strategy}:${chains}:${o.numeraire.address.toLowerCase()}:${path}`;
}

/** Price of tokenOut per tokenIn on a leg, in human units. 0 if underivable. */
function legPrice(leg: EngineLeg): number {
  const inH = scale(leg.amount_in, leg.token_in.decimals);
  const outH = scale(leg.amount_out, leg.token_out.decimals);
  if (inH <= 0) return 0;
  return outH / inH;
}

/**
 * Map one engine opportunity → dashboard opportunity. Throws on structurally
 * invalid input (missing numeraire / empty legs) so the caller can drop the bad
 * item and log it rather than surface a malformed row.
 *
 * @param o             the engine opportunity JSON
 * @param now           receipt time in epoch ms (defaults to Date.now())
 * @param ttlMs         how long the row stays live before pruning
 * @param originWallMs  ingestion tick-start wall clock (ms) for end-to-end latency;
 *                      omitted/0 when the frame carried no latency trace
 */
export function mapEngineOpportunity(
  o: EngineOpportunity,
  now: number = Date.now(),
  ttlMs: number = DEFAULT_TTL_MS,
  originWallMs?: number,
): ArbitrageOpportunity {
  if (!o || !o.numeraire || !Array.isArray(o.legs) || o.legs.length === 0) {
    throw new Error("invalid engine opportunity: missing numeraire or legs");
  }

  const dec = o.numeraire.decimals;
  const chainId = o.numeraire.chain_id || o.chain_ids?.[0] || o.block?.chain_id || 0;
  const network = NETWORKS_BY_CHAIN_ID[chainId];

  // Cross-chain destination: `chain_ids` lists every chain this opportunity
  // touches; the source is `chainId` (resolved above from the numeraire). The
  // destination is whichever *other* entry is present — but only when there is
  // unambiguously exactly one, so we never guess at a destination from
  // ambiguous data (root CLAUDE.md invariant 1: no fabricated data in a
  // runtime path). Resolved through the exact same chainId→key registry
  // lookup used for the source `network` field just above, including its
  // `chain-<id>` fallback for a chain id outside the registry.
  const isCrossChain = o.is_cross_chain === true;
  let destChainId: number | undefined;
  let destNetwork: string | undefined;
  if (isCrossChain) {
    const others = [...new Set(Array.isArray(o.chain_ids) ? o.chain_ids : [])].filter(
      (id) => id !== chainId,
    );
    const [onlyOther] = others;
    if (others.length === 1 && onlyOther !== undefined) {
      destChainId = onlyOther;
      const destNet = NETWORKS_BY_CHAIN_ID[destChainId];
      destNetwork = destNet?.key ?? `chain-${destChainId}`;
    }
  }

  const route: RouteLeg[] = o.legs.map((leg) => ({
    dex: shortPool(leg.pool),
    tokenIn: leg.token_in.symbol,
    tokenOut: leg.token_out.symbol,
    price: legPrice(leg),
    poolFeeBps: 0, // the detection engine does not surface per-pool fee on the leg
  }));

  // gas_cost and bridge_cost are already denominated in numeraire base units by
  // the engine; net_profit = gross_profit - gas_cost - bridge_cost. We fold the
  // (cross-chain only) bridge cost into the displayed gas/cost line and leave
  // flashLoanFeeUsd at 0 — the detection engine models no flash-loan fee (that
  // is an execution-layer cost, added later by the contracts adapter).
  const amountInUsd = scale(o.input_amount, dec);
  const grossProfitUsd = scale(o.gross_profit, dec);
  const gasCostUsd = scale(o.gas_cost, dec) + scale(o.bridge_cost, dec);
  const netProfitUsd = scale(o.net_profit, dec);

  return {
    id: opportunityId(o),
    ts: now,
    network: network?.key ?? `chain-${chainId}`,
    chainId,
    tokenIn: o.numeraire.symbol,
    route,
    amountInUsd,
    grossProfitUsd,
    flashLoanFeeUsd: 0,
    gasCostUsd,
    netProfitUsd,
    // Honest units: the `…Usd` figures above are real dollars only when the
    // numeraire is a USD stablecoin; otherwise they are numeraire base units.
    numeraireIsUsd: isUsdStable(o.numeraire.symbol),
    profitBps: num(o.profit_bps),
    // The engine does not expose a raw cross-venue spread; net profit in bps is
    // the closest honest proxy for the dashboard's spread column.
    spreadBps: num(o.profit_bps),
    confidence: Math.max(0, Math.min(1, num(o.risk?.success_probability))),
    status: "new",
    expiresAt: now + ttlMs,
    isCrossChain,
    ...(destChainId !== undefined ? { destChainId } : {}),
    ...(destNetwork !== undefined ? { destNetwork } : {}),
    ...(isCrossChain ? { settleSeconds: num(o.settle_seconds) } : {}),
    ...(originWallMs && originWallMs > 0 ? { originWallMs } : {}),
  };
}

/** Whether a numeraire symbol is a USD stablecoin (its `…Usd` fields are dollars). */
export function isUsdStable(symbol: string): boolean {
  return USD_STABLES.has(symbol.toUpperCase());
}
