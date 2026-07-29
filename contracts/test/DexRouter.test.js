const { expect } = require("chai");
const { ethers } = require("hardhat");
const { loadFixture } = require("@nomicfoundation/hardhat-network-helpers");
const { DexType, getAmountOut } = require("./helpers");

/**
 * Differential tests for DexRouter's Yul-encoded UNISWAP_V2 / UNISWAP_V3_SINGLE
 * call paths (see contracts/libraries/DexRouter.sol `_swapUniswapV2` /
 * `_swapUniswapV3Single`). Each hand-encoded call is checked against the same
 * reference constant-product formula already used elsewhere in this suite
 * (test/helpers.js `getAmountOut`, mirrored by MockUniV2/MockUniV3Router) —
 * if the raw calldata layout were wrong, these mocks would either revert
 * (wrong selector / misaligned words) or return a value that disagrees with
 * the reference math (misplaced field).
 */
describe("DexRouter (Yul-encoded dispatch)", () => {
  const e = (n, d = 18n) => BigInt(n) * 10n ** d;
  const FEE_BPS = 30n; // 0.30%

  async function fixture() {
    const ERC20 = await ethers.getContractFactory("MockERC20");
    const tokenA = await ERC20.deploy("Token A", "TKA", 18);
    const tokenB = await ERC20.deploy("Token B", "TKB", 18);

    const V2Pool = await ethers.getContractFactory("MockUniV2");
    const v2Pool = await V2Pool.deploy(tokenA.target, tokenB.target, FEE_BPS);
    await tokenA.mint(v2Pool.target, e(100_000));
    await tokenB.mint(v2Pool.target, e(100_000));

    const V3Router = await ethers.getContractFactory("MockUniV3Router");
    const v3Router = await V3Router.deploy(FEE_BPS);
    await tokenA.mint(v3Router.target, e(50_000));
    await tokenB.mint(v3Router.target, e(50_000));

    const Harness = await ethers.getContractFactory("DexRouterHarness");
    const harness = await Harness.deploy();
    // execute() runs in the harness's own context (internal library calls are
    // inlined), so the harness itself must hold the input token and is what
    // approves/receives on each hop.
    await tokenA.mint(harness.target, e(10_000));

    return { tokenA, tokenB, v2Pool, v3Router, harness };
  }

  function step(dexType, router, tokenIn, tokenOut, poolFee = 0, minOut = 0n) {
    return {
      dexType,
      router,
      tokenIn,
      tokenOut,
      poolFee,
      curveI: 0,
      curveJ: 0,
      minOut,
      data: "0x",
      amountInOffset: 0,
    };
  }

  async function v2Reserves(v2Pool, tokenA) {
    const [r0, r1] = await v2Pool.getReserves();
    const token0 = await v2Pool.token0();
    return token0.toLowerCase() === tokenA.target.toLowerCase() ? [r0, r1] : [r1, r0];
  }

  it("UNISWAP_V2: hand-encoded call matches the reference constant-product output", async () => {
    const { tokenA, tokenB, v2Pool, harness } = await loadFixture(fixture);
    const amountIn = e(100);
    const [rIn, rOut] = await v2Reserves(v2Pool, tokenA);
    const expected = getAmountOut(amountIn, rIn, rOut, FEE_BPS);

    const s = step(DexType.UNISWAP_V2, v2Pool.target, tokenA.target, tokenB.target);
    const before = await tokenB.balanceOf(harness.target);
    await harness.execute(s, amountIn, 0);
    const got = (await tokenB.balanceOf(harness.target)) - before;

    expect(got).to.equal(expected);
    expect(got).to.be.gt(0n);
  });

  it("UNISWAP_V3_SINGLE: hand-encoded call matches the reference constant-product output", async () => {
    const { tokenA, tokenB, v3Router, harness } = await loadFixture(fixture);
    const amountIn = e(100);
    const rIn = await tokenA.balanceOf(v3Router.target);
    const rOut = await tokenB.balanceOf(v3Router.target);
    const expected = getAmountOut(amountIn, rIn, rOut, FEE_BPS);

    const s = step(DexType.UNISWAP_V3_SINGLE, v3Router.target, tokenA.target, tokenB.target, 500);
    const before = await tokenB.balanceOf(harness.target);
    await harness.execute(s, amountIn, 0);
    const got = (await tokenB.balanceOf(harness.target)) - before;

    expect(got).to.equal(expected);
    expect(got).to.be.gt(0n);
  });

  it("UNISWAP_V2: a sweep of amounts all agree with the reference formula", async () => {
    // Each swap mutates the pool's live reserves, so the reference reserves
    // must be re-read before every iteration (not snapshotted once) — the
    // on-chain call always uses live reserves at call time.
    const { tokenA, tokenB, v2Pool, harness } = await loadFixture(fixture);

    for (const amountIn of [1n, e(1), e(10), e(500), e(2500), 1234567890123n]) {
      const [rIn, rOut] = await v2Reserves(v2Pool, tokenA);
      const expected = getAmountOut(amountIn, rIn, rOut, FEE_BPS);
      const s = step(DexType.UNISWAP_V2, v2Pool.target, tokenA.target, tokenB.target);
      const before = await tokenB.balanceOf(harness.target);
      await harness.execute(s, amountIn, 0);
      const got = (await tokenB.balanceOf(harness.target)) - before;
      expect(got, `amountIn=${amountIn}`).to.equal(expected);
    }
  });

  it("UNISWAP_V3_SINGLE: a sweep of amounts all agree with the reference formula", async () => {
    const { tokenA, tokenB, v3Router, harness } = await loadFixture(fixture);

    for (const amountIn of [1n, e(1), e(10), e(500), e(2500), 1234567890123n]) {
      const rIn = await tokenA.balanceOf(v3Router.target);
      const rOut = await tokenB.balanceOf(v3Router.target);
      const expected = getAmountOut(amountIn, rIn, rOut, FEE_BPS);
      const s = step(DexType.UNISWAP_V3_SINGLE, v3Router.target, tokenA.target, tokenB.target, 500);
      const before = await tokenB.balanceOf(harness.target);
      await harness.execute(s, amountIn, 0);
      const got = (await tokenB.balanceOf(harness.target)) - before;
      expect(got, `amountIn=${amountIn}`).to.equal(expected);
    }
  });

  it("UNISWAP_V2: bubbles the router's own revert reason (not swallowed into a generic error)", async () => {
    const { tokenA, tokenB, v2Pool, harness } = await loadFixture(fixture);
    const s = step(DexType.UNISWAP_V2, v2Pool.target, tokenA.target, tokenB.target, 0, e(999_999));
    await expect(harness.execute(s, e(100), 0)).to.be.revertedWithCustomError(v2Pool, "InsufficientOutput");
  });

  it("UNISWAP_V3_SINGLE: bubbles the router's own revert reason (not swallowed into a generic error)", async () => {
    const { tokenA, tokenB, v3Router, harness } = await loadFixture(fixture);
    const s = step(DexType.UNISWAP_V3_SINGLE, v3Router.target, tokenA.target, tokenB.target, 500, e(999_999));
    await expect(harness.execute(s, e(100), 0)).to.be.revertedWithCustomError(v3Router, "InsufficientOutput");
  });
});
