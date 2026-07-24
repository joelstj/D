/**
 * Per-chain address book for deployment and integration.
 *
 * ┌──────────────────────────────────────────────────────────────────────────┐
 * │  ⚠  VERIFY EVERY ADDRESS against the protocol's official docs before any   │
 * │     mainnet deployment or transaction. Addresses change, and a wrong       │
 * │     provider/router address will cause reverts or, worse, loss of funds.   │
 * │     Sources: Aave (aave.com/docs), Balancer (docs.balancer.fi),            │
 * │     Uniswap (docs.uniswap.org/contracts). `null` = not deployed / unknown  │
 * │     on that chain at time of writing — fill it in after verifying.         │
 * └──────────────────────────────────────────────────────────────────────────┘
 *
 * The FlashLoanArbitrage contract itself is address-agnostic: only `aavePool`
 * and `balancerVault` are needed at deploy time (constructor). DEX routers are
 * supplied per-call inside the route, so `dex` here is a convenience map for
 * bots/integration — not consumed by the constructor.
 */

const ZERO = "0x0000000000000000000000000000000000000000";

const CHAINS = {
  optimism: {
    chainId: 10,
    aavePool: "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
    balancerVault: "0xBA12222222228d8Ba445958a75a0704d566BF2C8",
    dex: {
      uniswapV3Router02: "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45",
      // UniV2-compatible: Velodrome uses a different (Solidly) router ABI — use GENERIC for it.
    },
    tokens: {
      WETH: "0x4200000000000000000000000000000000000006",
      USDC: "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85", // native USDC
      "USDC.e": "0x7F5c764cBc14f9669B88837ca1490cCa17c31607",
    },
  },
  base: {
    chainId: 8453,
    aavePool: "0xA238Dd80C259a72e81d7e4664a9801593F98d1c5",
    balancerVault: "0xBA12222222228d8Ba445958a75a0704d566BF2C8",
    dex: {
      uniswapV3Router02: "0x2626664c2603336E57B271c5C0b26F421741e481",
      // BaseSwap / SushiSwap V2-style routers exist; supply per-call.
    },
    tokens: {
      WETH: "0x4200000000000000000000000000000000000006",
      USDC: "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", // native USDC
    },
  },
  arbitrum: {
    chainId: 42161,
    aavePool: "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
    balancerVault: "0xBA12222222228d8Ba445958a75a0704d566BF2C8",
    dex: {
      uniswapV3Router02: "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45",
      sushiV2Router: "0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506",
    },
    tokens: {
      WETH: "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
      USDC: "0xaf88d065e77c8cC2239327C5EDb3A432268e5831", // native USDC
      "USDC.e": "0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8",
    },
  },
  polygon: {
    chainId: 137,
    aavePool: "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
    balancerVault: "0xBA12222222228d8Ba445958a75a0704d566BF2C8",
    dex: {
      uniswapV3Router02: "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45",
      quickswapV2Router: "0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff",
      sushiV2Router: "0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506",
    },
    tokens: {
      WMATIC: "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270",
      WETH: "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619",
      USDC: "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359", // native USDC
      "USDC.e": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
    },
  },
  // ── Chains where a canonical Aave/Balancer deployment was NOT confirmed at
  //    time of writing. Fill these in after verifying, or borrow via a provider
  //    that IS live on the chain. The contract accepts address(0) for a provider
  //    that is unused on a given chain (at least one must be non-zero).
  unichain: {
    chainId: 130,
    aavePool: null, // VERIFY: Aave V3 on Unichain — confirm Pool address
    balancerVault: null, // VERIFY: Balancer on Unichain
    dex: {
      // Uniswap is native to Unichain — verify SwapRouter02 / UniversalRouter.
      uniswapV3Router02: null,
    },
    tokens: {
      WETH: "0x4200000000000000000000000000000000000006", // OP-stack canonical (verify)
    },
  },
  ink: {
    chainId: 57073,
    aavePool: null, // VERIFY: Aave availability on Ink
    balancerVault: null, // VERIFY: Balancer availability on Ink
    dex: {},
    tokens: {
      WETH: "0x4200000000000000000000000000000000000006", // OP-stack canonical (verify)
    },
  },
};

/** Resolve the address bundle for a Hardhat network name. */
function forNetwork(name) {
  const c = CHAINS[name];
  if (!c) throw new Error(`No address book entry for network "${name}". Add it to config/addresses.js.`);
  return c;
}

module.exports = { CHAINS, forNetwork, ZERO };
