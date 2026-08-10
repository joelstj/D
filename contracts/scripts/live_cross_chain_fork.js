/**
 * LIVE CROSS-CHAIN EXECUTION — one real two-leg inventory-based cross-chain
 * arbitrage, on live state on TWO forked chains, sweeping the final settled
 * value to a real operator wallet.
 *
 *   POLYGON_RPC_URL=https://polygon.gateway.tenderly.co \
 *   ARBITRUM_RPC_URL=https://arb1.arbitrum.io/rpc \
 *   PROFIT_RECEIVER=0xYourMetaMaskAddress \
 *   npx hardhat run scripts/live_cross_chain_fork.js
 *
 * ## Why this isn't (and can't be) a flash loan
 *
 * See CrossChainArbitrageExecutor.sol's own NatSpec: a flash loan must be
 * borrowed and repaid inside one transaction on one chain; a transaction
 * cannot span two chains, so "atomic cross-chain flash-loan arbitrage" isn't
 * something the EVM can do. The real, implemented model is INVENTORY-BASED
 * and non-atomic — two separate transactions on two chains:
 *   Tx A (source chain):  buy the target asset with held inventory, bridge it.
 *   Tx B (dest chain):    once funds arrive, sell into the numeraire.
 * This script drives exactly that against real live-chain state on both
 * legs, then sweeps the result to a named wallet via the guardian-gated
 * rescue path — the operational mechanism this contract actually offers for
 * "get settled inventory out to me" (it deliberately holds inventory, unlike
 * FlashLoanArbitrage, which never should).
 *
 * ## What's real, what's simulated, what's manufactured
 *
 * Real: both chains forked at their live head block; the deployed bytecode
 * is this repo's real CrossChainArbitrageExecutor; every swap (the funding
 * swaps, the source-leg buy, the destination-leg sell) runs through real
 * QuickSwap/Uniswap-V3 pools at their live reserves/price.
 * Simulated: the bridge itself — no real IBridgeAdapter exists in this repo
 * yet (docs/notes-cross-chain-flash-loan-gaps.md finding C1), so
 * MockBridgeAdapter stands in, delivering the measured source-leg output 1:1
 * as real WETH on Arbitrum via WETH9.deposit(), exactly as in
 * test/fork/CrossChainDualFork.test.js.
 * Manufactured: the price dislocation the source leg trades on. A funded
 * "manipulator" account sells real, organically-acquired WETH into the
 * Polygon WETH/USDC.e pool immediately before the executor buys — the same
 * technique every other live-fork proof in this repo uses
 * (live_flash_loan_fork.js, test/fork/*.test.js), disclosed the same way.
 * The Arbitrum sell leg is NOT manipulated — it executes at Arbitrum's
 * ordinary, untouched market price. The reported profit is the genuine,
 * measured difference between what the executor spent (Polygon) and what it
 * delivered (Arbitrum) for the SAME numeraire (USDC.e), not an estimate.
 *
 * A successful run proves the two-leg pipeline is fully operational against
 * live infrastructure on two chains and pays the wallet it is told to — it
 * is not evidence of a standing, risk-free mainnet opportunity. Nothing here
 * is broadcast; `assertForkOnly` (reused unmodified from
 * live_flash_loan_fork.js) refuses every network but the in-process fork.
 */
const hre = require("hardhat");
const { ethers, network } = hre;
const { resolveProfitReceiver } = require("./live_flash_loan_fork");
const { DexType, getAmountOut } = require("../test/helpers");

/**
 * Refuse to run anywhere except the in-process fork. Same safety property as
 * `assertForkOnly` in live_flash_loan_fork.js (network must be "hardhat", so
 * writes stay local and are discarded) but this script forks TWICE at
 * runtime via `hardhat_reset` rather than once via hardhat.config.js's
 * static `forking` block, so it checks POLYGON_RPC_URL/ARBITRUM_RPC_URL
 * instead of FORK_RPC_URL. Pure function of (networkName, env), unit-tested
 * directly — see test/CrossChainLiveForkScript.test.js.
 */
function assertDualForkOnly(networkName, env) {
  if (networkName !== "hardhat") {
    throw new Error(
      `Refusing to run on network "${networkName}". This script executes a real cross-chain arbitrage and ` +
        `must only ever run against the in-process fork (root CLAUDE.md §2 invariant 3: the loop ` +
        `never broadcasts). Run it without --network, with POLYGON_RPC_URL and ARBITRUM_RPC_URL set.`
    );
  }
  if (!env.POLYGON_RPC_URL || !env.ARBITRUM_RPC_URL) {
    throw new Error("Set both POLYGON_RPC_URL and ARBITRUM_RPC_URL (source/destination chains).");
  }
}

const POLYGON = {
  name: "Polygon PoS",
  WMATIC: "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270",
  WETH: "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619",
  USDCe: "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
  QUICKSWAP_ROUTER: "0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff",
};

const ARBITRUM = {
  name: "Arbitrum One",
  WETH: "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1", // real WETH9 — deposit() works here
  USDCe: "0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8",
  UNIV3_ROUTER02: "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45",
  UNIV3_FEE: 500,
};

const ERC20_ABI = [
  "function deposit() payable",
  "function approve(address,uint256) returns (bool)",
  "function transfer(address,uint256) returns (bool)",
  "function balanceOf(address) view returns (uint256)",
];
const V2_ROUTER_ABI = ["function swapExactTokensForTokens(uint256,uint256,address[],address,uint256) returns (uint256[])"];

async function resetFork(rpcUrl) {
  await network.provider.request({ method: "hardhat_reset", params: [{ forking: { jsonRpcUrl: rpcUrl } }] });
}

async function fundNative(address, amountWei) {
  await ethers.provider.send("hardhat_setBalance", [address, "0x" + amountWei.toString(16)]);
}

function v2StepRaw(router, tokenIn, tokenOut) {
  return { dexType: DexType.UNISWAP_V2, router, tokenIn, tokenOut, poolFee: 0, curveI: 0, curveJ: 0, minOut: 0n, data: "0x", amountInOffset: 0 };
}

function v3Step(router, tokenIn, tokenOut, fee) {
  return { dexType: DexType.UNISWAP_V3_SINGLE, router, tokenIn, tokenOut, poolFee: fee, curveI: 0, curveJ: 0, minOut: 0n, data: "0x", amountInOffset: 0 };
}

async function getReservesFor(factory, tokenA, tokenB, provider) {
  const pairAddr = await factory.getPair(tokenA, tokenB);
  if (pairAddr === ethers.ZeroAddress) throw new Error(`No live QuickSwap pair for ${tokenA}/${tokenB}.`);
  const pair = await ethers.getContractAt(
    ["function getReserves() view returns (uint112,uint112,uint32)", "function token0() view returns (address)"],
    pairAddr,
    provider
  );
  const [r0, r1] = await pair.getReserves();
  const token0 = (await pair.token0()).toLowerCase();
  return { pairAddr, reserveA: token0 === tokenA.toLowerCase() ? r0 : r1, reserveB: token0 === tokenA.toLowerCase() ? r1 : r0 };
}

async function main() {
  assertDualForkOnly(hre.network.name, process.env);
  const receiver = resolveProfitReceiver(process.env);

  console.log("\n─────────────────────────────────────────────────────────────");
  console.log(" LIVE CROSS-CHAIN EXECUTION (dual forked live state — not broadcast)");
  console.log("─────────────────────────────────────────────────────────────");
  console.log(`  route            : ${POLYGON.name} (source) -> ${ARBITRUM.name} (destination)`);
  console.log(`  profit receiver  : ${receiver.address}`);
  console.log(`  receiver source  : ${receiver.source}`);

  // ================= Leg 1 — Polygon (source chain) =================
  await resetFork(process.env.POLYGON_RPC_URL);
  const polyBlock = await ethers.provider.getBlockNumber();
  if ((await ethers.provider.getCode(POLYGON.WETH)) === "0x") throw new Error("POLYGON_RPC_URL is not a Polygon PoS fork.");
  console.log(`\n  forked Polygon at block ${polyBlock}`);

  const [admin, bot, manipulator] = await ethers.getSigners();

  const XChain = await ethers.getContractFactory("CrossChainArbitrageExecutor");
  const sourceExec = await XChain.deploy(admin.address);
  await sourceExec.grantRole(await sourceExec.EXECUTOR_ROLE(), bot.address);
  const Bridge = await ethers.getContractFactory("MockBridgeAdapter");
  const bridge = await Bridge.deploy();
  await sourceExec.setBridgeAdapterAllowed(bridge.target, true);

  const wmatic = await ethers.getContractAt(ERC20_ABI, POLYGON.WMATIC);
  const weth = await ethers.getContractAt(ERC20_ABI, POLYGON.WETH);
  const usdcE = await ethers.getContractAt(ERC20_ABI, POLYGON.USDCe);
  const quickswap = await ethers.getContractAt(["function factory() view returns (address)"], POLYGON.QUICKSWAP_ROUTER);
  const factory = await ethers.getContractAt(["function getPair(address,address) view returns (address)"], await quickswap.factory());
  const v2Router = await ethers.getContractAt(V2_ROUTER_ABI, POLYGON.QUICKSWAP_ROUTER);

  // Snapshot the WETH/USDC.e pool BEFORE anything trades against it — needed
  // both to size the dump and to compute the honest counterfactual later.
  const { reserveA: usdcReserveBefore, reserveB: wethReserveBefore } = await getReservesFor(factory, POLYGON.USDCe, POLYGON.WETH);
  console.log(`  live WETH/USDC.e reserves : ${ethers.formatUnits(usdcReserveBefore, 6)} USDC.e / ${ethers.formatEther(wethReserveBefore)} WETH`);

  // Fund the manipulator with native MATIC purely to wrap/swap on this local
  // fork (standard `hardhat_setBalance` fork-testing primitive — discarded on
  // exit, not fabricated market data). Two organic (unmanipulated-pool) swaps
  // fund: (a) the executor's starting USDC.e inventory, (b) the WETH the
  // manipulator will dump.
  await fundNative(manipulator.address, ethers.parseEther("800000"));
  await (await wmatic.connect(manipulator).deposit({ value: ethers.parseEther("750000") })).wait();
  await (await wmatic.connect(manipulator).approve(POLYGON.QUICKSWAP_ROUTER, ethers.MaxUint256)).wait();

  // (a) WMATIC -> USDC.e, funds the executor's real starting inventory.
  await (
    await v2Router.connect(manipulator).swapExactTokensForTokens(
      ethers.parseEther("50000"), 0, [POLYGON.WMATIC, POLYGON.USDCe], manipulator.address, 99999999999n
    )
  ).wait();
  const executorUsdc = await usdcE.balanceOf(manipulator.address);
  await (await usdcE.connect(manipulator).transfer(sourceExec.target, executorUsdc)).wait();
  console.log(`  funded executor  : ${ethers.formatUnits(executorUsdc, 6)} USDC.e (organic WMATIC->USDC.e swap, undisturbed pool)`);

  // (b) WMATIC -> WETH, organically acquiring the WETH the manipulator dumps.
  await (
    await v2Router.connect(manipulator).swapExactTokensForTokens(
      ethers.parseEther("700000"), 0, [POLYGON.WMATIC, POLYGON.WETH], manipulator.address, 99999999999n
    )
  ).wait();
  const dumpWeth = await weth.balanceOf(manipulator.address);

  // The manufactured dislocation: sell that WETH into the WETH/USDC.e pool,
  // suppressing WETH's USDC.e price immediately before the executor buys.
  await (await weth.connect(manipulator).approve(POLYGON.QUICKSWAP_ROUTER, ethers.MaxUint256)).wait();
  await (
    await v2Router.connect(manipulator).swapExactTokensForTokens(
      dumpWeth, 0, [POLYGON.WETH, POLYGON.USDCe], manipulator.address, 99999999999n
    )
  ).wait();
  console.log(`  dislocation      : dumped ${ethers.formatEther(dumpWeth)} WETH into the live WETH/USDC.e pool`);

  // ---- Source leg: executor buys WETH with its USDC.e at the now-cheap price, bridges it. ----
  const sourceSteps = [v2StepRaw(POLYGON.QUICKSWAP_ROUTER, POLYGON.USDCe, POLYGON.WETH)];
  await expectOk(
    sourceExec.connect(bot).executeSourceLeg(
      sourceSteps, POLYGON.USDCe, executorUsdc, bridge.target, POLYGON.WETH, 1n, 42161, bot.address, "0x"
    )
  );
  const bridgedWeth = await weth.balanceOf(bridge.target);
  if (bridgedWeth <= 0n) throw new Error("Source leg produced nothing to bridge.");
  const residualPoly = await weth.balanceOf(sourceExec.target);
  if (residualPoly !== 0n) throw new Error(`Source executor retained ${residualPoly} WETH — it must keep nothing.`);
  console.log(`  source leg       : bought ${ethers.formatEther(bridgedWeth)} WETH with ${ethers.formatUnits(executorUsdc, 6)} USDC.e, bridged it`);

  // Honest, same-unit counterfactual: what the SAME USDC.e input would have
  // bought against the pool's PRE-dump reserves. Pure function of numbers
  // already fetched from chain — no external price feed, nothing hardcoded.
  const counterfactualWeth = getAmountOut(executorUsdc, usdcReserveBefore, wethReserveBefore);
  const dislocationCapture = bridgedWeth - counterfactualWeth;

  // ================= Leg 2 — Arbitrum (destination chain) =================
  await resetFork(process.env.ARBITRUM_RPC_URL);
  const arbBlock = await ethers.provider.getBlockNumber();
  if ((await ethers.provider.getCode(ARBITRUM.WETH)) === "0x") throw new Error("ARBITRUM_RPC_URL is not an Arbitrum One fork.");
  console.log(`\n  forked Arbitrum at block ${arbBlock}`);

  const XChain2 = await ethers.getContractFactory("CrossChainArbitrageExecutor");
  const destExec = await XChain2.deploy(admin.address);
  await destExec.grantRole(await destExec.EXECUTOR_ROLE(), bot.address);

  const wethArb = await ethers.getContractAt(ERC20_ABI, ARBITRUM.WETH);
  const usdcArb = await ethers.getContractAt(ERC20_ABI, ARBITRUM.USDCe);

  // Bridge delivery is the one deliberately simulated step (see file header):
  // the measured source-leg output arrives 1:1 as real Arbitrum WETH.
  await fundNative(manipulator.address, bridgedWeth + ethers.parseEther("1"));
  await (await wethArb.connect(manipulator).deposit({ value: bridgedWeth })).wait();
  await (await wethArb.connect(manipulator).transfer(destExec.target, bridgedWeth)).wait();

  // Destination leg sells at Arbitrum's ORDINARY, untouched market price —
  // no manipulation on this side.
  await expectOk(
    destExec.connect(bot).executeDestinationLeg(
      [v3Step(ARBITRUM.UNIV3_ROUTER02, ARBITRUM.WETH, ARBITRUM.USDCe, ARBITRUM.UNIV3_FEE)], ARBITRUM.WETH, 0n, 1n
    )
  );
  const settledUsdc = await usdcArb.balanceOf(destExec.target);
  console.log(`  destination leg  : sold ${ethers.formatEther(bridgedWeth)} WETH for ${ethers.formatUnits(settledUsdc, 6)} USDC.e (real, untouched Uniswap V3 price)`);

  // ================= Sweep to the real operator wallet =================
  const before = await usdcArb.balanceOf(receiver.address);
  await (await destExec.connect(admin).rescueTokens(ARBITRUM.USDCe, receiver.address, 0n)).wait(); // 0 => sweep full balance
  const after = await usdcArb.balanceOf(receiver.address);
  const delivered = after - before;

  const residualArb = await usdcArb.balanceOf(destExec.target);
  if (residualArb !== 0n) throw new Error(`Destination executor retained ${residualArb} after sweep.`);
  if (delivered !== settledUsdc) throw new Error(`Delivered ${delivered} != settled ${settledUsdc}.`);

  const profit = delivered - executorUsdc;

  console.log("\n─────────────────────────────────────────────────────────────");
  console.log(`  ✅ DELIVERED: ${ethers.formatUnits(delivered, 6)} USDC.e -> ${receiver.address}`);
  console.log("─────────────────────────────────────────────────────────────");
  console.log(`  spent (Polygon, source leg input)      : ${ethers.formatUnits(executorUsdc, 6)} USDC.e`);
  console.log(`  received (Arbitrum, swept to wallet)   : ${ethers.formatUnits(delivered, 6)} USDC.e`);
  console.log(`  net                                    : ${profit >= 0n ? "+" : ""}${ethers.formatUnits(profit, 6)} USDC.e`);
  console.log(`  of the ${ethers.formatEther(bridgedWeth)} WETH bridged, ${ethers.formatEther(dislocationCapture)} WETH (${ethers.formatEther(counterfactualWeth)} WETH counterfactual) is attributable to the manufactured dislocation`);
  console.log("\n  NOTE: both legs ran against real forked live state (real pools, real");
  console.log("        reserves/price). The bridge is simulated (no real IBridgeAdapter");
  console.log("        exists yet) and the Polygon-side price was manufactured, exactly");
  console.log("        as every other live-fork proof in this repo discloses. This shows");
  console.log("        the two-leg pipeline is fully operational end to end and pays the");
  console.log("        named wallet — it is not evidence of a standing mainnet opportunity.\n");

  if (profit <= 0n) throw new Error("Round trip did not net a profit — dislocation was not large enough relative to fees/slippage.");
}

async function expectOk(txPromise) {
  const tx = await txPromise;
  const receipt = await tx.wait();
  if (receipt.status !== 1) throw new Error("Transaction reverted.");
  return receipt;
}

if (require.main === module) {
  main().catch((err) => {
    console.error(`\n❌ ${err.message}\n`);
    process.exitCode = 1;
  });
}

module.exports = { assertDualForkOnly, POLYGON, ARBITRUM };
