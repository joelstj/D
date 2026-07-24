const { ethers } = require("hardhat");

// DexType enum (see contracts/libraries/ArbTypes.sol)
const DexType = {
  UNISWAP_V2: 0,
  UNISWAP_V3_SINGLE: 1,
  UNISWAP_V3_MULTI: 2,
  CURVE: 3,
  GENERIC: 4,
};

// FlashProvider enum
const Provider = {
  AAVE_V3: 0,
  BALANCER_V2: 1,
};

const MAX_DEADLINE = 99999999999n; // year 5138

/**
 * Build a SwapStep tuple in the exact field order the ABI expects.
 */
function v2Step(router, tokenIn, tokenOut, minOut = 0n) {
  return {
    dexType: DexType.UNISWAP_V2,
    router,
    tokenIn,
    tokenOut,
    poolFee: 0,
    curveI: 0,
    curveJ: 0,
    minOut,
    data: "0x",
    amountInOffset: 0,
  };
}

/** Constant-product output with a bps fee (mirrors MockUniV2/OptimalArbitrage). */
function getAmountOut(amountIn, reserveIn, reserveOut, feeBps = 30n) {
  const amountInWithFee = amountIn * (10000n - feeBps);
  return (amountInWithFee * reserveOut) / (reserveIn * 10000n + amountInWithFee);
}

module.exports = { DexType, Provider, MAX_DEADLINE, v2Step, getAmountOut, ethers };
