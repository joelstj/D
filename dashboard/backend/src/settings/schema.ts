import { z } from "zod";

/**
 * The full set of flash-loan arbitrage parameters a user can "dial in" from the
 * GUI. Every field is validated and bounded so a bad value from any client
 * (browser, script, another language's SDK) can never put the engine into an
 * unsafe state. Changes take effect immediately — see SettingsStore.
 */
export const SettingsSchema = z
  .object({
    /** Master switch: when false the engine stops scanning. */
    engineEnabled: z.boolean().default(true),

    /** paper = simulate fills only; live = build & broadcast real transactions. */
    executionMode: z.enum(["paper", "live"]).default("paper"),

    /** When true the engine auto-executes qualifying opportunities. */
    autoExecute: z.boolean().default(false),

    /** L2 networks to scan (keys from /api/networks). */
    networks: z.array(z.string()).min(1).default(["base", "arbitrum"]),

    /** Asset that flash loans are denominated in. */
    baseToken: z.string().min(1).default("USDC"),

    /** Token universe considered for routes. */
    tokens: z.array(z.string()).min(2).default(["USDC", "WETH", "USDT", "DAI", "WBTC"]),

    /** DEX venue keys to consider. */
    dexes: z
      .array(z.string())
      .min(1)
      .default(["uniswap-v3", "aerodrome", "camelot", "sushiswap"]),

    /** Flash-loan provider. */
    flashLoanProvider: z.enum(["aave-v3", "balancer-v2", "uniswap-v3"]).default("aave-v3"),

    /** Size of each flash loan in USD. */
    loanAmountUsd: z.number().positive().max(100_000_000).default(50_000),

    /** Minimum net profit (USD) for an opportunity to qualify. */
    minProfitUsd: z.number().min(0).default(25),

    /** Minimum net profit in basis points of loan size. */
    minProfitBps: z.number().min(0).max(10_000).default(8),

    /** Maximum tolerated slippage in basis points. */
    slippageBps: z.number().min(0).max(5_000).default(30),

    /** Gas price ceiling (gwei). L2 gas is typically well under 1 gwei. */
    maxGasGwei: z.number().min(0).default(0.5),

    /** Priority fee (gwei). */
    priorityFeeGwei: z.number().min(0).default(0.01),

    /** Gas limit for the arbitrage bundle. */
    gasLimit: z.number().int().positive().default(1_500_000),

    /** How often the engine scans for opportunities (ms). */
    scanIntervalMs: z.number().int().min(250).max(60_000).default(1500),

    /** Maximum simultaneous in-flight executions. */
    maxConcurrentTrades: z.number().int().min(1).max(50).default(3),

    /** Cooldown between executions on the same network (ms). */
    cooldownMs: z.number().int().min(0).default(4000),

    /** Transaction deadline (seconds). */
    deadlineSec: z.number().int().min(1).max(3600).default(30),

    /** Halt auto-execution once realized loss for the day exceeds this (USD). */
    maxDailyLossUsd: z.number().min(0).default(500),

    /** Maximum notional per position (USD). */
    maxPositionUsd: z.number().min(0).default(250_000),
  })
  .strict();

export type Settings = z.infer<typeof SettingsSchema>;

/** Fully-defaulted settings object. */
export const DEFAULT_SETTINGS: Settings = SettingsSchema.parse({});

/** Partial schema used to validate PATCH payloads. */
export const SettingsPatchSchema = SettingsSchema.partial();
export type SettingsPatch = z.infer<typeof SettingsPatchSchema>;
