const { expect } = require("chai");
const { ethers } = require("hardhat");
const { loadFixture } = require("@nomicfoundation/hardhat-network-helpers");
const { DexType, Provider, MAX_DEADLINE, v2Step } = require("../helpers");

/**
 * LIVE MAINNET-FORK SUITE — Polygon PoS.
 *
 * Runs only when FORK_RPC_URL points at a Polygon RPC:
 *   FORK_RPC_URL=https://polygon.gateway.tenderly.co npx hardhat test test/fork/PolygonFork.test.js
 *
 * Mirrors test/fork/ArbitrumFork.test.js (same manufactured-dislocation
 * technique, same assertions), pointed at Polygon's live contracts instead:
 *   - a real Aave V3 / Balancer V2 flash loan,
 *   - a real Uniswap V3 swap (SwapRouter02 0x68b3…Fc45 — same address as on
 *     Optimism/Arbitrum; see contracts/config/addresses.js),
 *   - a real QuickSwap V2 swap (Polygon's native UniswapV2-style DEX).
 *
 * Uses WMATIC (not WETH) as the borrowed/arb'd asset: Polygon's native gas
 * token is MATIC, so WMATIC — not the bridged WETH — is the canonical
 * wrapped-native-token contract with a `deposit()` payable function.
 * Polygon's WETH (0x7ceB23fD…) is a plain bridged ERC20 (minted/burned by the
 * PoS bridge) with no such function — confirmed empirically: an earlier
 * version of this test tried `weth.deposit()` here and got a bare revert.
 * WMATIC is also Polygon's highest-liquidity pairing token, so if anything
 * this is a *more* representative pair than WETH/USDC.e would have been.
 *
 * QuickSwap's factory address is looked up live via the router's own
 * `factory()` call rather than hardcoded — the router address is already
 * verified in config/addresses.js; deriving the factory on-chain avoids
 * committing a second guessed address (see docs/notes-cross-chain-flash-
 * loans.md, "do not invent addresses").
 *
 * The profitable case deliberately MANUFACTURES a price dislocation (dumps
 * WMATIC into the smaller QuickSwap pool, then captures the gap against the
 * deeper Uniswap V3 pool) so the test is deterministic — it proves the
 * CONTRACT is fully operational against live Polygon infrastructure, not
 * that risk-free profit is lying around on mainnet.
 */

const AAVE_POOL = "0x794a61358D6845594F94dc1DB02A252b5b4814aD";
const BALANCER_VAULT = "0xBA12222222228d8Ba445958a75a0704d566BF2C8";
const WMATIC = "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270"; // wraps native MATIC; also used for fork self-detection
const USDCe = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"; // bridged USDC.e
const UNIV3_ROUTER02 = "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45";
const QUICKSWAP_ROUTER = "0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff";

/// QuickSwap's WMATIC/USDC.e pool holds several million WMATIC (WMATIC trades
/// several orders of magnitude below WETH, so an equivalent-depth pool holds
/// far more raw tokens) — comfortably more than a default Hardhat test
/// account's native balance. `hardhat_setBalance` gives the manipulator
/// exactly what this run's manufactured dump needs, computed from the live
/// reserve read moments earlier; it's a standard fork-testing cheat (the
/// Foundry equivalent is `vm.deal`), not fabricated market data — the pool
/// reserves and swap prices it then trades against are all real.
async function fundNative(address, amountWei) {
  await ethers.provider.send("hardhat_setBalance", [address, "0x" + amountWei.toString(16)]);
}

function v3SingleStep(router, tokenIn, tokenOut, fee, minOut = 0n) {
  return {
    dexType: DexType.UNISWAP_V3_SINGLE,
    router,
    tokenIn,
    tokenOut,
    poolFee: fee,
    curveI: 0,
    curveJ: 0,
    minOut,
    data: "0x",
    amountInOffset: 0,
  };
}

describe("FlashLoanArbitrage — Polygon PoS mainnet fork (live contracts)", function () {
  before(async function () {
    if (!process.env.FORK_RPC_URL) {
      console.log("      (skipped: set FORK_RPC_URL to a Polygon RPC to run)");
      this.skip();
    }
    // A Hardhat fork keeps its local chainId (31337) while serving the remote
    // chain's STATE, so detect Polygon by the presence of its live, chain-
    // specific WMATIC contract (unlike the Aave Pool, whose address is shared
    // across several chains and wouldn't distinguish them).
    const wmaticCode = await ethers.provider.getCode(WMATIC);
    if (wmaticCode === "0x") {
      console.log("      (skipped: FORK_RPC_URL is not a Polygon PoS fork)");
      this.skip();
    }
  });

  async function fixture() {
    const [admin, bot, receiver, manipulator] = await ethers.getSigners();
    const Arb = await ethers.getContractFactory("FlashLoanArbitrage");
    const arb = await Arb.deploy(AAVE_POOL, BALANCER_VAULT, admin.address);
    await arb.grantRole(await arb.EXECUTOR_ROLE(), bot.address);

    const wmatic = await ethers.getContractAt(
      [
        "function deposit() payable",
        "function approve(address,uint256) returns (bool)",
        "function balanceOf(address) view returns (uint256)",
      ],
      WMATIC
    );
    const quickswapRouter = await ethers.getContractAt(["function factory() view returns (address)"], QUICKSWAP_ROUTER);
    const quickswapFactoryAddr = await quickswapRouter.factory();
    const quickswapFactory = await ethers.getContractAt(
      ["function getPair(address,address) view returns (address)"],
      quickswapFactoryAddr
    );
    const pairAddr = await quickswapFactory.getPair(WMATIC, USDCe);
    const pair = await ethers.getContractAt(
      [
        "function getReserves() view returns (uint112,uint112,uint32)",
        "function token0() view returns (address)",
      ],
      pairAddr
    );
    return { admin, bot, receiver, manipulator, arb, wmatic, pair, pairAddr };
  }

  it("finds a live QuickSwap WMATIC/USDC.e pair with real reserves", async () => {
    const { pairAddr, pair } = await loadFixture(fixture);
    expect(pairAddr).to.not.equal(ethers.ZeroAddress);
    const [r0, r1] = await pair.getReserves();
    expect(r0).to.be.gt(0n);
    expect(r1).to.be.gt(0n);
  });

  it("reads the live Aave V3 flash-loan premium", async () => {
    const { arb } = await loadFixture(fixture);
    const pool = await ethers.getContractAt(
      ["function FLASHLOAN_PREMIUM_TOTAL() view returns (uint128)"],
      AAVE_POOL
    );
    const onchain = await pool.FLASHLOAN_PREMIUM_TOTAL();
    expect(await arb.aavePremiumBps()).to.equal(onchain);
    expect(onchain).to.be.gt(0n);
  });

  it("reverts atomically when no real arbitrage exists (prices aligned)", async () => {
    const { arb, bot, receiver } = await loadFixture(fixture);
    const params = {
      provider: Provider.BALANCER_V2,
      asset: WMATIC,
      amount: ethers.parseEther("1000"),
      minProfit: 1n,
      profitReceiver: receiver.address,
      deadline: MAX_DEADLINE,
      steps: [
        v3SingleStep(UNIV3_ROUTER02, WMATIC, USDCe, 3000),
        v2Step(QUICKSWAP_ROUTER, USDCe, WMATIC),
      ],
    };
    await expect(arb.connect(bot).executeArbitrage(params)).to.be.revertedWithCustomError(
      arb,
      "InsufficientProfit"
    );
  });

  it("executes a real atomic cross-DEX flash-loan arb and banks the profit", async () => {
    const { arb, bot, receiver, manipulator, wmatic, pair } = await loadFixture(fixture);

    const [r0, r1] = await pair.getReserves();
    const token0 = await pair.token0();
    const wmaticReserve = token0.toLowerCase() === WMATIC.toLowerCase() ? r0 : r1;

    // 1) Manufacture a dislocation: dump ~50% of the pool's WMATIC into QuickSwap.
    const dump = (wmaticReserve * 50n) / 100n;
    const wrapAmount = dump + ethers.parseEther("1000");
    await fundNative(manipulator.address, wrapAmount + ethers.parseEther("1000")); // headroom for gas, on top of the wrapped value
    await wmatic.connect(manipulator).deposit({ value: wrapAmount });
    await wmatic.connect(manipulator).approve(QUICKSWAP_ROUTER, ethers.MaxUint256);
    const quickswap = await ethers.getContractAt(
      ["function swapExactTokensForTokens(uint256,uint256,address[],address,uint256) returns (uint256[])"],
      QUICKSWAP_ROUTER
    );
    await quickswap
      .connect(manipulator)
      .swapExactTokensForTokens(dump, 0, [WMATIC, USDCe], manipulator.address, MAX_DEADLINE);

    // 2) Capture it: borrow a modest slice, sell high on Uniswap V3, buy back cheap on QuickSwap.
    const borrow = (wmaticReserve * 1n) / 1000n; // 0.1% - Uniswap V3 leg appears much shallower than QuickSwap's raw reserves
    const minProfit = ethers.parseEther("0.01");
    const params = {
      provider: Provider.BALANCER_V2,
      asset: WMATIC,
      amount: borrow,
      minProfit,
      profitReceiver: receiver.address,
      deadline: MAX_DEADLINE,
      steps: [
        v3SingleStep(UNIV3_ROUTER02, WMATIC, USDCe, 3000),
        v2Step(QUICKSWAP_ROUTER, USDCe, WMATIC),
      ],
    };

    const before = await wmatic.balanceOf(receiver.address);
    await expect(arb.connect(bot).executeArbitrage(params)).to.emit(arb, "ArbitrageExecuted");
    const profit = (await wmatic.balanceOf(receiver.address)) - before;

    console.log(`      captured profit: ${ethers.formatEther(profit)} WMATIC on a ${ethers.formatEther(borrow)} WMATIC loan`);
    expect(profit).to.be.gt(minProfit);
    expect(await wmatic.balanceOf(await arb.getAddress())).to.equal(0n);
  });

  it("executes the same arb borrowing from live Aave V3 (net of premium)", async () => {
    const { arb, bot, receiver, manipulator, wmatic, pair } = await loadFixture(fixture);

    const [r0, r1] = await pair.getReserves();
    const token0 = await pair.token0();
    const wmaticReserve = token0.toLowerCase() === WMATIC.toLowerCase() ? r0 : r1;

    const dump = (wmaticReserve * 50n) / 100n;
    const wrapAmount = dump + ethers.parseEther("1000");
    await fundNative(manipulator.address, wrapAmount + ethers.parseEther("1000")); // headroom for gas, on top of the wrapped value
    await wmatic.connect(manipulator).deposit({ value: wrapAmount });
    await wmatic.connect(manipulator).approve(QUICKSWAP_ROUTER, ethers.MaxUint256);
    const quickswap = await ethers.getContractAt(
      ["function swapExactTokensForTokens(uint256,uint256,address[],address,uint256) returns (uint256[])"],
      QUICKSWAP_ROUTER
    );
    await quickswap
      .connect(manipulator)
      .swapExactTokensForTokens(dump, 0, [WMATIC, USDCe], manipulator.address, MAX_DEADLINE);

    const borrow = (wmaticReserve * 1n) / 1000n; // 0.1% - Uniswap V3 leg appears much shallower than QuickSwap's raw reserves
    const minProfit = ethers.parseEther("0.01");
    const params = {
      provider: Provider.AAVE_V3,
      asset: WMATIC,
      amount: borrow,
      minProfit,
      profitReceiver: receiver.address,
      deadline: MAX_DEADLINE,
      steps: [v3SingleStep(UNIV3_ROUTER02, WMATIC, USDCe, 3000), v2Step(QUICKSWAP_ROUTER, USDCe, WMATIC)],
    };

    const before = await wmatic.balanceOf(receiver.address);
    await expect(arb.connect(bot).executeArbitrage(params)).to.emit(arb, "ArbitrageExecuted");
    const profit = (await wmatic.balanceOf(receiver.address)) - before;

    console.log(`      captured profit (Aave): ${ethers.formatEther(profit)} WMATIC on a ${ethers.formatEther(borrow)} WMATIC loan`);
    expect(profit).to.be.gt(minProfit);
    expect(await wmatic.balanceOf(await arb.getAddress())).to.equal(0n);
  });
});
