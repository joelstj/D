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
      uniswapV3Factory: "0x1F98431c8aD98523631AE4a59f267346ea31F984",
      // Velodrome is Solidly-style (variable curve, not plain constant-product) —
      // factory listed for reference; use GENERIC for its swaps, not a v2 router call.
      velodromeV2Factory: "0xF1046053aa5682b4F9a81b5481394DA16BE5FF5a",
    },
    tokens: {
      WETH: "0x4200000000000000000000000000000000000006",
      USDC: "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85", // native USDC
      "USDC.e": "0x7F5c764cBc14f9669B88837ca1490cCa17c31607",
      USDT: "0x94b008aA00579c1307B0EF2c499aD98a8ce58e58",
      DAI: "0xDA10009cBd5D07dd0CeCc66161FC93D7c9000da1",
      WBTC: "0x68f180fcCe6836688e9084f035309E29Bf0A2095",
      LINK: "0x350A791Bfc2C21F9Ed5d10980Dad2e2638ffa7f6",
      UNI: "0x6fd9d7AD17242c41f7131d257212c54A0e816691",
      AAVE: "0x76FB31fb4af56892A25e32cFC43De717950c9278", // underlying AAVE, not the aToken
      wstETH: "0x1F32b1c2345538c0c6f582fCB022739c4A194Ebb",
      cbETH: "0xadDb6A0412DE1BA0F936DCAEb8Aaa24578dcF3B2",
      rETH: "0x9Bcef72be871e61ED4fBbc7630889beE758eb81D",
      CRV: "0x0994206dfE8De6Ec6920FF4D779B0d950605Fb53",
      FRAX: "0x2E3D870790dC77A83DD1d18184Acc7439A53f475",
      LDO: "0xFdb794692724153d1488CCdBE0C56c252596735f",
    },
  },
  base: {
    chainId: 8453,
    aavePool: "0xA238Dd80C259a72e81d7e4664a9801593F98d1c5",
    balancerVault: "0xBA12222222228d8Ba445958a75a0704d566BF2C8",
    dex: {
      uniswapV3Router02: "0x2626664c2603336E57B271c5C0b26F421741e481",
      uniswapV3Factory: "0x33128a8fC17869897dcE68Ed026d694621f6FDfD", // NOT the cross-chain-default factory address
      baseSwapV2Factory: "0xFDa619b6d20975be80A10332cD39b9a4b0FAa8BB", // plain constant-product fork
      // Aerodrome is Base's deepest-liquidity DEX but Solidly-style (variable
      // curve) — factory listed for reference; use GENERIC for its swaps.
      aerodromeFactory: "0x420DD381b31aEf6683db6B902084cB0FFEce40Da",
    },
    tokens: {
      WETH: "0x4200000000000000000000000000000000000006",
      USDC: "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", // native USDC
      USDT: "0xfde4C96c8593536E31F229EA8f37b2ADa2699bb2", // BaseScan: not issued/redeemable by Tether
      DAI: "0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb",
      WBTC: "0x1ceA84203673764244E05693e42E6Ace62bE9BA5",
      LINK: "0x88Fb150BDc53A65fe94Dea0c9BA0a6dAf8C6e196",
      UNI: "0xc3De830EA07524a0761646A6a4E4be0E114a3C83",
      AAVE: "0x63706e401c06Ac8513145b7687A14804d17F814b",
      wstETH: "0xc1CBa3fCea344f92D9239c08C0568f6F2F0ee452",
      cbETH: "0x2Ae3F1Ec7F1F5012CFEab0185bfc7aa3cf0DEc22", // cbETH's home chain
      rETH: "0xB6fe221Fe9EeF5aBa221c348bA20A1Bf5e73624c",
      CRV: "0x8Ee73c484A26e0A5df2Ee2a4960B789967dd0415",
    },
  },
  arbitrum: {
    chainId: 42161,
    aavePool: "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
    balancerVault: "0xBA12222222228d8Ba445958a75a0704d566BF2C8",
    dex: {
      uniswapV3Router02: "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45",
      uniswapV3Factory: "0x1F98431c8aD98523631AE4a59f267346ea31F984",
      sushiV2Router: "0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506",
      camelotV2Factory: "0x6EcCab422D763aC031210895C81787E87B43A652", // plain constant-product fork
    },
    tokens: {
      WETH: "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
      USDC: "0xaf88d065e77c8cC2239327C5EDb3A432268e5831", // native USDC
      "USDC.e": "0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8",
      USDT: "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9", // displays as USD₮0 post-rebrand, same contract
      DAI: "0xDA10009cBd5D07dd0CeCc66161FC93D7c9000da1",
      WBTC: "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f",
      LINK: "0xf97f4df75117a78c1A5a0DBb814Af92458539FB4",
      UNI: "0xFa7F8980b0f1E64A2062791cc3b0871572f1F7f0",
      AAVE: "0xba5DdD1f9d7F570dc94a51479a000E3BCE967196",
      wstETH: "0x5979D7b546E38E414F7E9822514be443A4800529",
      cbETH: "0x1DEBd73E752beaf79865Fd6446b0c970EAe7732f",
      rETH: "0xEC70Dcb4A1EFa46b8F2D97C310C9c4790ba5ffA8",
      CRV: "0x11cDb42B0EB46D95f990BeDD4695A6e3fA034978",
      FRAX: "0x17FC002b466eEc40DaE837Fc4bE5c67993ddBd6F",
      LDO: "0x13ad51ed4F1B7e9Dc168d8a00cB3F4dDD85EfA60",
      ARB: "0x912CE59144191C1204E64559FE8253a0e49E6548", // native to Arbitrum only, no other-chain rep
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
      // Uniswap is native to Unichain but liquidity is overwhelmingly V4
      // (singleton) — the SwapRouter02 address could not be disambiguated
      // from several verified-but-unofficial-looking candidates; left null
      // rather than guessed. The V3 factory IS confirmed, and is NOT the
      // cross-chain-default factory address (Unichain got its own deployment).
      uniswapV3Router02: null,
      uniswapV3Factory: "0x1F98400000000000000000000000000000000003",
    },
    tokens: {
      WETH: "0x4200000000000000000000000000000000000006", // confirmed live (OP-stack predeploy)
      USDC: "0x078D782b760474a361dDA0AF3839290b0EF57AD6", // native USDC (Circle CCTP)
      USDT: "0x588CE4F028D8e7B53B687865d6A67b3A54C75518",
      DAI: "0x20CAb320A855b39F724131C69424240519573f81",
      WBTC: "0x927B51f251480a681271180DA4de28D44EC4AfB8",
      LINK: "0x5a53B6D19D8EDCb7923F0D840EeBB3f09BBeEfB7",
      UNI: "0x8f187aA05619a017077f5308904739877ce9eA21", // Uniswap's own governance token, on Uniswap's own chain
      AAVE: "0x02a24C380dA560E4032Dc6671d8164cfbEEAAE1e",
      wstETH: "0xc02fE7317D4eb8753a02c35fe019786854A92001",
      cbETH: "0xEb64b50FeF2A363940369285F86Ae9a68211db59", // real but thin liquidity (~32 holders at time of writing)
    },
  },
  ink: {
    chainId: 57073,
    aavePool: null, // VERIFY: Aave availability on Ink
    balancerVault: null, // VERIFY: Balancer availability on Ink
    dex: {
      uniswapV3Factory: "0x640887A9ba3A9C53Ed27D0F7e8246A4F933f3424", // confirmed live via Ink's own Blockscout
    },
    tokens: {
      WETH: "0x4200000000000000000000000000000000000006", // confirmed live (OP-stack predeploy)
      USDC: "0x2D270e6886d130D724215A266106e6832161EAEd", // native USDC (Circle CCTP)
      // DAI/WBTC/wstETH/cbETH/LINK/UNI/AAVE: no credible non-scam deployment
      // found on Ink at time of writing — left absent rather than guessed.
      // Ink's own USDT equivalent is a distinct token (USDT0, LayerZero OFT
      // standard), not the same contract as "USDT" elsewhere, so it is
      // deliberately not listed under that symbol here.
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
