const { expect } = require("chai");
const { ethers } = require("hardhat");
const { loadFixture } = require("@nomicfoundation/hardhat-network-helpers");
const { DexType, Provider, MAX_DEADLINE, v2Step } = require("../helpers");

/**
 * LIVE MAINNET-FORK SUITE — Arbitrum One.
 *
 * Runs only when FORK_RPC_URL points at an Arbitrum RPC:
 *   FORK_RPC_URL=https://arb1.arbitrum.io/rpc npx hardhat test test/fork/ArbitrumFork.test.js
 *
 * It exercises the deployed FlashLoanArbitrage against REAL, live contracts:
 *   - a real Balancer V2 flash loan (Vault 0xBA12…F2C8, 0 fee),
 *   - a real Uniswap V3 swap (SwapRouter02 0x68b3…Fc45),
 *   - a real SushiSwap V2 swap (Router 0x1b02…7506),
 * proving the full atomic borrow → cross-DEX multi-hop → repay → profit path
 * works end to end on-chain.
 *
 * The profitable case deliberately MANUFACTURES a price dislocation (it dumps
 * WETH into the smaller Sushi pool, then captures the gap) so the test is
 * deterministic. That verifies the CONTRACT is fully operational against live
 * infrastructure — not that risk-free profit is lying around on mainnet.
 *
 * NOTE on the flash provider: the executable arbitrage legs below borrow from
 * Balancer V2 because Aave V3's Pool (a proxy that delegatecalls external logic
 * libraries) does not execute inside a Hardhat/EDR mainnet fork served by a
 * public RPC — a bare, correct Aave receiver reverts with empty data there too,
 * so this is a fork-tooling limitation, not a contract bug. Aave works on real
 * chains; the Aave callback path is fully covered by the offline unit tests
 * (MockAavePool), and the live Aave Pool is still read below to prove real
 * integration. See docs/DEPLOYMENT.md for details.
 */

const AAVE_POOL = "0x794a61358D6845594F94dc1DB02A252b5b4814aD";
const BALANCER_VAULT = "0xBA12222222228d8Ba445958a75a0704d566BF2C8";
const WETH = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1";
const USDCe = "0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8";
const UNIV3_ROUTER02 = "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45";
const SUSHI_ROUTER = "0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506";
const SUSHI_FACTORY = "0xc35DADB65012eC5796536bD9864eD8773aBc74C4";

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

describe("FlashLoanArbitrage — Arbitrum mainnet fork (live contracts)", function () {
  before(async function () {
    if (!process.env.FORK_RPC_URL) {
      console.log("      (skipped: set FORK_RPC_URL to an Arbitrum RPC to run)");
      this.skip();
    }
    // A Hardhat fork keeps its local chainId (31337) while serving the remote
    // chain's STATE, so detect Arbitrum by the presence of its live contracts.
    const [aaveCode, wethCode] = await Promise.all([
      ethers.provider.getCode(AAVE_POOL),
      ethers.provider.getCode(WETH),
    ]);
    if (aaveCode === "0x" || wethCode === "0x") {
      console.log("      (skipped: FORK_RPC_URL is not an Arbitrum One fork)");
      this.skip();
    }
  });

  async function fixture() {
    const [admin, bot, receiver, manipulator] = await ethers.getSigners();
    const Arb = await ethers.getContractFactory("FlashLoanArbitrage");
    const arb = await Arb.deploy(AAVE_POOL, BALANCER_VAULT, admin.address);
    await arb.grantRole(await arb.EXECUTOR_ROLE(), bot.address);

    const weth = await ethers.getContractAt(
      [
        "function deposit() payable",
        "function approve(address,uint256) returns (bool)",
        "function balanceOf(address) view returns (uint256)",
      ],
      WETH
    );
    const sushiFactory = await ethers.getContractAt(
      ["function getPair(address,address) view returns (address)"],
      SUSHI_FACTORY
    );
    const pairAddr = await sushiFactory.getPair(WETH, USDCe);
    const pair = await ethers.getContractAt(
      [
        "function getReserves() view returns (uint112,uint112,uint32)",
        "function token0() view returns (address)",
      ],
      pairAddr
    );
    return { admin, bot, receiver, manipulator, arb, weth, pair, pairAddr };
  }

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
    // Round-trip WETH -> USDC.e (Uni V3) -> WETH (Sushi) with markets aligned
    // loses the swap fees, so the min-profit guard must revert.
    const params = {
      provider: Provider.BALANCER_V2,
      asset: WETH,
      amount: ethers.parseEther("1"),
      minProfit: 1n,
      profitReceiver: receiver.address,
      deadline: MAX_DEADLINE,
      steps: [
        v3SingleStep(UNIV3_ROUTER02, WETH, USDCe, 500),
        v2Step(SUSHI_ROUTER, USDCe, WETH),
      ],
    };
    await expect(arb.connect(bot).executeArbitrage(params)).to.be.revertedWithCustomError(
      arb,
      "InsufficientProfit"
    );
  });

  it("executes a real atomic cross-DEX flash-loan arb and banks the profit", async () => {
    const { arb, bot, receiver, manipulator, weth, pair } = await loadFixture(fixture);

    // Live Sushi WETH reserve determines sizing so the test scales with the pool.
    const [r0, r1] = await pair.getReserves();
    const token0 = await pair.token0();
    const wethReserve = token0.toLowerCase() === WETH.toLowerCase() ? r0 : r1;

    // 1) Manufacture a dislocation: dump ~50% of the pool's WETH into Sushi,
    //    pushing WETH cheap there relative to the deep Uniswap V3 pool.
    const dump = (wethReserve * 50n) / 100n;
    await weth.connect(manipulator).deposit({ value: dump + ethers.parseEther("1") });
    await weth.connect(manipulator).approve(SUSHI_ROUTER, ethers.MaxUint256);
    const sushi = await ethers.getContractAt(
      [
        "function swapExactTokensForTokens(uint256,uint256,address[],address,uint256) returns (uint256[])",
      ],
      SUSHI_ROUTER
    );
    await sushi
      .connect(manipulator)
      .swapExactTokensForTokens(dump, 0, [WETH, USDCe], manipulator.address, MAX_DEADLINE);

    // 2) Capture it: borrow a modest slice, sell high on Uniswap, buy back cheap on Sushi.
    const borrow = (wethReserve * 2n) / 100n;
    const minProfit = ethers.parseEther("0.001");
    const params = {
      provider: Provider.BALANCER_V2,
      asset: WETH,
      amount: borrow,
      minProfit,
      profitReceiver: receiver.address,
      deadline: MAX_DEADLINE,
      steps: [
        v3SingleStep(UNIV3_ROUTER02, WETH, USDCe, 500),
        v2Step(SUSHI_ROUTER, USDCe, WETH),
      ],
    };

    const before = await weth.balanceOf(receiver.address);
    await expect(arb.connect(bot).executeArbitrage(params)).to.emit(arb, "ArbitrageExecuted");
    const profit = (await weth.balanceOf(receiver.address)) - before;

    console.log(`      captured profit: ${ethers.formatEther(profit)} WETH on a ${ethers.formatEther(borrow)} WETH loan`);
    expect(profit).to.be.gt(minProfit);
    // The engine must never retain the borrowed asset after settling.
    expect(await weth.balanceOf(await arb.getAddress())).to.equal(0n);
  });
});
