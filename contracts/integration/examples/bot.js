/**
 * Plug-and-play integration example (Node.js / ethers v6).
 *
 * Any language with an EVM ABI encoder can drive the contract identically —
 * this is just a reference. The flow is:
 *   1. (optional) size the loan with quoteOptimalTwoHopV2,
 *   2. build the SwapStep[] route,
 *   3. SIMULATE with eth_call (staticCall) — a revert means "not profitable",
 *   4. send executeArbitrage only if the simulation succeeds.
 *
 *   npm i ethers
 *   ARB_ADDRESS=0x... RPC_URL=... PRIVATE_KEY=... node integration/examples/bot.js
 */
const { ethers } = require("ethers");
const fs = require("fs");
const path = require("path");

const ABI = JSON.parse(
  fs.readFileSync(path.join(__dirname, "..", "abi", "FlashLoanArbitrage.abi.json"))
);

// Enums (must match contracts/libraries/ArbTypes.sol)
const FlashProvider = { AAVE_V3: 0, BALANCER_V2: 1 };
const DexType = {
  UNISWAP_V2: 0,
  UNISWAP_V3_SINGLE: 1,
  UNISWAP_V3_MULTI: 2,
  CURVE: 3,
  GENERIC: 4,
};

/** Helper builders keep the (large) SwapStep tuple readable. */
function v2(router, tokenIn, tokenOut, minOut = 0n) {
  return { dexType: DexType.UNISWAP_V2, router, tokenIn, tokenOut, poolFee: 0, curveI: 0, curveJ: 0, minOut, data: "0x", amountInOffset: 0 };
}
function v3(router, tokenIn, tokenOut, fee, minOut = 0n) {
  return { dexType: DexType.UNISWAP_V3_SINGLE, router, tokenIn, tokenOut, poolFee: fee, curveI: 0, curveJ: 0, minOut, data: "0x", amountInOffset: 0 };
}

async function main() {
  const provider = new ethers.JsonRpcProvider(process.env.RPC_URL);
  const signer = new ethers.Wallet(process.env.PRIVATE_KEY, provider);
  const arb = new ethers.Contract(process.env.ARB_ADDRESS, ABI, signer);

  // --- addresses for your chain (example values: Arbitrum One) ---
  const WETH = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1";
  const USDCe = "0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8";
  const UNIV3 = "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45";
  const SUSHI = "0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506";

  // 1) Build the route: borrow WETH, sell on Uni V3, buy back on Sushi V2.
  const steps = [v3(UNIV3, WETH, USDCe, 500), v2(SUSHI, USDCe, WETH)];

  const params = {
    provider: FlashProvider.BALANCER_V2, // 0-fee where available
    asset: WETH,
    amount: ethers.parseEther("1"), // or size via arb.quoteOptimalTwoHopV2(...)
    minProfit: ethers.parseEther("0.002"), // your profit floor (covers gas + margin)
    profitReceiver: signer.address,
    deadline: Math.floor(Date.now() / 1000) + 60,
    steps,
  };

  // 2) SIMULATE first — a revert here means the opportunity isn't profitable
  //    right now (the custom error InsufficientProfit reports the shortfall).
  try {
    await arb.executeArbitrage.staticCall(params);
  } catch (e) {
    console.log("Not profitable at this moment:", e.shortMessage || e.message);
    return;
  }

  // 3) Send it. Consider a private mempool / bundle for MEV protection.
  const tx = await arb.executeArbitrage(params);
  console.log("submitted:", tx.hash);
  const rcpt = await tx.wait();
  console.log("mined in block", rcpt.blockNumber);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
