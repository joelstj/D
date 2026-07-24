const { expect } = require("chai");
const { ethers } = require("hardhat");
const { getAmountOut } = require("./helpers");

// Property tests for the optimal-sizing library and its Yul sqrt.
describe("OptimalArbitrage library", () => {
  const e = (n, d = 18n) => BigInt(n) * 10n ** d;

  async function deploy() {
    const H = await ethers.getContractFactory("OptimalArbitrageHarness");
    return await H.deploy();
  }

  it("sqrt matches the integer floor of the real square root", async () => {
    const h = await deploy();
    const cases = [0n, 1n, 2n, 3n, 4n, 15n, 16n, 99n, 100n, 10n ** 18n, (10n ** 18n) * 7n + 3n, 2n ** 200n];
    for (const x of cases) {
      const z = await h.sqrt(x);
      expect(z * z).to.be.lte(x);
      expect((z + 1n) * (z + 1n)).to.be.gt(x);
    }
  });

  it("getAmountOut agrees with the reference constant-product formula", async () => {
    const h = await deploy();
    const out = await h.getAmountOut(e(1000, 6n), e(180000, 6n), e(100), 30n);
    expect(out).to.equal(getAmountOut(e(1000, 6n), e(180000, 6n), e(100), 30n));
  });

  it("returns zero when there is no profitable arbitrage", async () => {
    const h = await deploy();
    // Identical pools => no cross-pool price gap to exploit.
    const [amountIn, profit] = await h.optimalV2Amount(e(1000), e(1000), e(1000), e(1000), 30n);
    expect(amountIn).to.equal(0n);
    expect(profit).to.equal(0n);
  });

  it("finds the profit-maximising size (beats a dense grid of alternatives)", async () => {
    const h = await deploy();
    // Buy pool A: 180000 USDC / 100 WETH. Sell pool B: 220000 USDC / 100 WETH.
    const rInA = e(180000, 6n);
    const rOutA = e(100);
    const rInB = e(100);
    const rOutB = e(220000, 6n);
    const feeBps = 30n;

    const [amountIn, expectedProfit] = await h.optimalV2Amount(rInA, rOutA, rInB, rOutB, feeBps);
    expect(amountIn).to.be.gt(0n);
    expect(expectedProfit).to.be.gt(0n);

    const profitAt = (x) => {
      const o1 = getAmountOut(x, rInA, rOutA, feeBps);
      const o2 = getAmountOut(o1, rInB, rOutB, feeBps);
      return o2 > x ? o2 - x : 0n;
    };

    expect(profitAt(amountIn)).to.equal(expectedProfit);
    // No sampled size within ±50% (5% steps) should beat the closed-form optimum.
    for (let pct = 50n; pct <= 150n; pct += 5n) {
      if (pct === 100n) continue;
      const alt = (amountIn * pct) / 100n;
      expect(expectedProfit).to.be.gte(profitAt(alt));
    }
  });
});
