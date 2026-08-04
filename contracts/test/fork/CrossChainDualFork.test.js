const { expect } = require("chai");
const { ethers, network } = require("hardhat");
const { DexType, v2Step } = require("../helpers");

/**
 * LIVE DUAL-CHAIN FORK SUITE — cross-chain flash-loan-funded arbitrage,
 * Polygon PoS <-> Arbitrum One.
 *
 * Runs only when BOTH POLYGON_RPC_URL and ARBITRUM_RPC_URL are set:
 *   POLYGON_RPC_URL=https://polygon.gateway.tenderly.co \
 *   ARBITRUM_RPC_URL=https://arb1.arbitrum.io/rpc \
 *     npx hardhat test test/fork/CrossChainDualFork.test.js
 *
 * WHAT THIS PROVES, PRECISELY: there is no such thing as an atomic
 * cross-chain flash loan — a single EVM transaction cannot span two chains,
 * so "borrow on chain A, use on chain B, repay on chain A, all atomically"
 * is not something any contract here claims (see the contract-level notice
 * in contracts/crosschain/CrossChainArbitrageExecutor.sol and
 * docs/specs/10-cross-chain.md). What IS real and IS proven here is the
 * actual production model: an inventory-based, two-transaction flow —
 *   1. Source leg on a real Polygon fork: swap live WMATIC inventory into
 *      WETH via a real QuickSwap pool, dispatch it to a bridge adapter.
 *   2. Destination leg on a real Arbitrum fork: receive the bridged value,
 *      swap it into USDC.e via a real Uniswap V3 pool.
 * Hardhat's `hardhat_reset` re-points the SAME in-process chain at a
 * different fork mid-test, so both legs run against genuinely live,
 * independently-fetched mainnet state from two different chains in one
 * coherent test — not two mocked chains.
 *
 * The ONLY simulated step is the bridge/relayer itself (no real bridge
 * relayer can act on an ephemeral local fork) — exactly what
 * docs/specs/10-cross-chain.md's own testing section calls for: "Cross-chain
 * messaging is mocked deterministically in tests (no live bridge calls in
 * the loop)." Concretely: the amount of WETH measured leaving the source
 * chain is delivered 1:1 as WETH on the destination chain (via `deposit()`,
 * the same real WETH9 contract, just Arbitrum's own instance of it) — a
 * simplification of a real bridge (which would charge a fee / have latency),
 * clearly labeled as such, not presented as a live bridge integration.
 */

const POLYGON = {
  AAVE_POOL: "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
  WMATIC: "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270",
  WETH: "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619", // bridged WETH (bridge TARGET on the source leg's swap output, not deposited directly)
  QUICKSWAP_ROUTER: "0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff",
};

const ARBITRUM = {
  WETH: "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1", // real WETH9 - deposit() works here
  USDCe: "0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8",
  UNIV3_ROUTER02: "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45",
};

async function resetFork(rpcUrl) {
  await network.provider.request({
    method: "hardhat_reset",
    params: [{ forking: { jsonRpcUrl: rpcUrl } }],
  });
}

async function fundNative(address, amountWei) {
  await ethers.provider.send("hardhat_setBalance", [address, "0x" + amountWei.toString(16)]);
}

function v2StepRaw(router, tokenIn, tokenOut) {
  return { dexType: DexType.UNISWAP_V2, router, tokenIn, tokenOut, poolFee: 0, curveI: 0, curveJ: 0, minOut: 0n, data: "0x", amountInOffset: 0 };
}

describe("CrossChainArbitrageExecutor — dual real fork (Polygon source -> Arbitrum destination)", function () {
  this.timeout(180_000);

  before(function () {
    if (!process.env.POLYGON_RPC_URL || !process.env.ARBITRUM_RPC_URL) {
      console.log("      (skipped: set POLYGON_RPC_URL and ARBITRUM_RPC_URL to run)");
      this.skip();
    }
  });

  it("executes the source leg on a real Polygon fork, then the destination leg on a real Arbitrum fork", async function () {
    const [admin, bot, manipulator] = await ethers.getSigners();

    // ---------------------------------------------------------------
    // Leg 1 — Polygon (source chain): swap real WMATIC inventory into
    // WETH via a real QuickSwap pool, dispatch it to a bridge adapter.
    // ---------------------------------------------------------------
    await resetFork(process.env.POLYGON_RPC_URL);
    const wmaticCode = await ethers.provider.getCode(POLYGON.WMATIC);
    expect(wmaticCode, "FORK_RPC_URL is not a Polygon PoS fork").to.not.equal("0x");

    const XChain = await ethers.getContractFactory("CrossChainArbitrageExecutor");
    const sourceExec = await XChain.deploy(admin.address);
    await sourceExec.grantRole(await sourceExec.EXECUTOR_ROLE(), bot.address);

    const Bridge = await ethers.getContractFactory("MockBridgeAdapter");
    const bridge = await Bridge.deploy();
    // Deny-by-default: executeSourceLeg only accepts an allowlisted bridge
    // adapter (see CrossChainArbitrageExecutor.sol's allowedBridgeAdapters).
    await sourceExec.setBridgeAdapterAllowed(bridge.target, true);

    const wmaticOnPolygon = await ethers.getContractAt(
      ["function deposit() payable", "function approve(address,uint256) returns (bool)", "function transfer(address,uint256) returns (bool)"],
      POLYGON.WMATIC
    );
    const wethOnPolygon = await ethers.getContractAt(["function balanceOf(address) view returns (uint256)"], POLYGON.WETH);

    // Pre-position inventory on the source executor (matches how a real
    // deployment would hold working capital) — wrap MATIC as the test EOA,
    // then transfer it onto the executor.
    const seedAmount = ethers.parseEther("1000"); // 1000 WMATIC of inventory
    await fundNative(manipulator.address, seedAmount + ethers.parseEther("10"));
    await wmaticOnPolygon.connect(manipulator).deposit({ value: seedAmount });
    await wmaticOnPolygon.connect(manipulator).transfer(sourceExec.target, seedAmount);

    const quickswapRouter = await ethers.getContractAt(["function factory() view returns (address)"], POLYGON.QUICKSWAP_ROUTER);
    const quickswapFactory = await ethers.getContractAt(
      ["function getPair(address,address) view returns (address)"],
      await quickswapRouter.factory()
    );
    const wmaticWethPair = await quickswapFactory.getPair(POLYGON.WMATIC, POLYGON.WETH);
    expect(wmaticWethPair, "no live QuickSwap WMATIC/WETH pair found").to.not.equal(ethers.ZeroAddress);

    const sourceSteps = [v2StepRaw(POLYGON.QUICKSWAP_ROUTER, POLYGON.WMATIC, POLYGON.WETH)];
    await expect(
      sourceExec
        .connect(bot)
        .executeSourceLeg(
          sourceSteps,
          POLYGON.WMATIC,
          seedAmount,
          bridge.target,
          POLYGON.WETH,
          1n, // minBridgeAmount - just a nonzero floor, this test isn't about sizing
          42161, // dstChainId (Arbitrum One) - recorded in the event only
          bot.address, // dstRecipient - informational for a real bridge; unused by the mock
          "0x"
        )
    ).to.emit(sourceExec, "SourceLegDispatched");

    const bridgedWeth = await wethOnPolygon.balanceOf(bridge.target);
    expect(bridgedWeth, "source leg produced nothing to bridge").to.be.gt(0n);
    console.log(`      Polygon source leg: bridged ${ethers.formatEther(bridgedWeth)} WETH to the (mock) bridge adapter`);

    // The source executor never ends up holding the intermediate WETH — it
    // was fully forwarded to the bridge, matching the "no idle funds" posture
    // this component shares with FlashLoanArbitrage. Must be checked NOW,
    // while the provider still serves Polygon state (the next step re-points
    // it at Arbitrum, after which POLYGON.WETH is not a meaningful address).
    expect(await wethOnPolygon.balanceOf(sourceExec.target)).to.equal(0n);

    // ---------------------------------------------------------------
    // Leg 2 — Arbitrum (destination chain): the bridge/relayer step is
    // the one deliberately simulated part (see file header) — deliver
    // the same amount of value as real Arbitrum WETH via deposit(), then
    // run the destination leg against a real Uniswap V3 pool.
    // ---------------------------------------------------------------
    await resetFork(process.env.ARBITRUM_RPC_URL);
    const arbWethCode = await ethers.provider.getCode(ARBITRUM.WETH);
    expect(arbWethCode, "ARBITRUM_RPC_URL is not an Arbitrum One fork").to.not.equal("0x");

    const XChain2 = await ethers.getContractFactory("CrossChainArbitrageExecutor");
    const destExec = await XChain2.deploy(admin.address);
    await destExec.grantRole(await destExec.EXECUTOR_ROLE(), bot.address);

    const wethOnArbitrum = await ethers.getContractAt(
      ["function deposit() payable", "function transfer(address,uint256) returns (bool)", "function balanceOf(address) view returns (uint256)"],
      ARBITRUM.WETH
    );
    await fundNative(manipulator.address, bridgedWeth + ethers.parseEther("1"));
    await wethOnArbitrum.connect(manipulator).deposit({ value: bridgedWeth });
    await wethOnArbitrum.connect(manipulator).transfer(destExec.target, bridgedWeth);

    const usdcOnArbitrum = await ethers.getContractAt(["function balanceOf(address) view returns (uint256)"], ARBITRUM.USDCe);
    const destSteps = [
      {
        dexType: DexType.UNISWAP_V3_SINGLE,
        router: ARBITRUM.UNIV3_ROUTER02,
        tokenIn: ARBITRUM.WETH,
        tokenOut: ARBITRUM.USDCe,
        poolFee: 500,
        curveI: 0,
        curveJ: 0,
        minOut: 0n,
        data: "0x",
        amountInOffset: 0,
      },
    ];

    await expect(
      destExec.connect(bot).executeDestinationLeg(destSteps, ARBITRUM.WETH, 0n, 1n)
    ).to.emit(destExec, "DestinationLegSettled");

    const finalUsdc = await usdcOnArbitrum.balanceOf(destExec.target);
    expect(finalUsdc, "destination leg produced no USDC.e").to.be.gt(0n);
    console.log(`      Arbitrum destination leg: settled into ${ethers.formatUnits(finalUsdc, 6)} USDC.e`);
  });
});
