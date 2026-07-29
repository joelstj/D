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

  it("getAmountOut (Yul) agrees with the reference formula across a wide sweep of inputs", async () => {
    // Differential test for the Yul rewrite (contracts/CLAUDE.md: every
    // assembly block gets a test against a reference implementation). Sweeps
    // tiny/large amounts, lopsided/balanced reserves, and the full fee range,
    // including the documented edge cases (amountIn/reserveIn/reserveOut == 0,
    // feeBps == 0, feeBps just under BPS).
    const h = await deploy();
    const amounts = [0n, 1n, 2n, e(1), e(1000), e(1_000_000), 123456789012345n];
    const reserves = [0n, 1n, e(1), e(100_000), e(50_000_000)];
    const fees = [0n, 1n, 30n, 500n, 3000n, 9999n];
    const BPS = 10_000n;
    // test/helpers.js's getAmountOut is the raw formula only (no guard clause,
    // and callers before this test never exercised zero reserves) — mirror the
    // Solidity function's own early-return-zero guard here rather than change
    // a shared helper other tests also rely on.
    const reference = (amountIn, reserveIn, reserveOut, feeBps) =>
      amountIn === 0n || reserveIn === 0n || reserveOut === 0n || feeBps >= BPS
        ? 0n
        : getAmountOut(amountIn, reserveIn, reserveOut, feeBps);

    let compared = 0;
    for (const amountIn of amounts) {
      for (const reserveIn of reserves) {
        for (const reserveOut of reserves) {
          for (const feeBps of fees) {
            const onchain = await h.getAmountOut(amountIn, reserveIn, reserveOut, feeBps);
            const expected = reference(amountIn, reserveIn, reserveOut, feeBps);
            expect(onchain, `amountIn=${amountIn} reserveIn=${reserveIn} reserveOut=${reserveOut} feeBps=${feeBps}`)
              .to.equal(expected);
            compared++;
          }
        }
      }
    }
    expect(compared).to.equal(amounts.length * reserves.length * reserves.length * fees.length);
  });

  it("getAmountOut (Yul) rejects fees at or above BPS (the assembly path is never reached)", async () => {
    const h = await deploy();
    for (const feeBps of [10_000n, 10_001n, 2n ** 32n]) {
      expect(await h.getAmountOut(e(100), e(100_000), e(100_000), feeBps)).to.equal(0n);
    }
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
