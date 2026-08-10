/**
 * Proves a REAL, ALREADY-DEPLOYED FlashLoanArbitrage can execute an arbitrage.
 *
 *   FORK_RPC_URL=<base-rpc> DEPLOYED_ADDRESS=0x… PROFIT_RECEIVER=0x… \
 *   npx hardhat run scripts/verify_deployment_executes.js
 *
 * Distinct from scripts/live_flash_loan_fork.js, which deploys a fresh executor
 * onto the fork. This one forks the chain at its current head — so the deployed
 * contract already exists in fork state — and drives THAT contract's real,
 * on-chain bytecode. It is the difference between "the code we compiled works"
 * and "the thing actually sitting at that address works".
 *
 * Route: WETH →[UniV3 0.05%]→ USDC →[UniV3 0.30%]→ WETH. Both hops use the same
 * verified SwapRouter02, so no address beyond config/addresses.js is introduced
 * (contracts golden rule 7). The dislocation between the two fee tiers is
 * MANUFACTURED, exactly as the fork suites do — this proves operability, not
 * that free profit exists on Base.
 *
 * Safety: fork-only, enforced by the same guard as live_flash_loan_fork.js.
 * Nothing is broadcast. The executor role is exercised via Hardhat's
 * impersonation cheat code, which works only on a local fork.
 */
const hre = require("hardhat");
const { ethers } = hre;
const { assertForkOnly, resolveProfitReceiver } = require("./live_flash_loan_fork");

const MAX_DEADLINE = 99999999999n;
const Provider = { AAVE_V3: 0, BALANCER_V2: 1 };

// All three verified in config/addresses.js under `base`.
const WETH = "0x4200000000000000000000000000000000000006";
const USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913";
const V3_ROUTER = "0x2626664c2603336E57B271c5C0b26F421741e481";

const ERC20_ABI = [
  "function deposit() payable",
  "function approve(address,uint256) returns (bool)",
  "function balanceOf(address) view returns (uint256)",
];

function v3Step(tokenIn, tokenOut, fee) {
  return {
    dexType: 1, // UNISWAP_V3_SINGLE
    router: V3_ROUTER,
    tokenIn,
    tokenOut,
    poolFee: fee,
    curveI: 0,
    curveJ: 0,
    minOut: 0n,
    data: "0x",
    amountInOffset: 0,
  };
}

async function main() {
  assertForkOnly(hre.network.name, process.env);

  const address = process.env.DEPLOYED_ADDRESS;
  if (!address || !ethers.isAddress(address)) {
    throw new Error("Set DEPLOYED_ADDRESS to the deployed FlashLoanArbitrage address.");
  }
  const receiver = resolveProfitReceiver(process.env);

  const code = await ethers.provider.getCode(address);
  if (code === "0x") throw new Error(`No contract at ${address} on this fork.`);

  // Advance one block past the fork point before executing anything. An
  // `eth_call` at exactly the fork block is treated as *historical* execution,
  // which needs a hardfork-activation history the forked L2 doesn't publish;
  // blocks mined locally after the fork use the configured hardfork instead.
  await ethers.provider.send("hardhat_mine", ["0x1"]);

  console.log("\n───────────────────────────────────────────────────────────────");
  console.log(" EXECUTING AGAINST THE REAL DEPLOYED CONTRACT (forked live state)");
  console.log("───────────────────────────────────────────────────────────────");
  console.log(`  forked at block  : ${await ethers.provider.getBlockNumber()}`);
  console.log(`  deployed contract: ${ethers.getAddress(address)}`);
  console.log(`  on-chain bytecode: ${code.length / 2 - 1} bytes`);
  console.log(`  profit receiver  : ${receiver.address}`);

  const arb = await ethers.getContractAt("FlashLoanArbitrage", address);
  console.log(`  aavePremiumBps() : ${await arb.aavePremiumBps()} (live read)`);

  // The deployer holds EXECUTOR_ROLE. Impersonate it — a fork-only cheat code
  // (Foundry's vm.prank equivalent); no key is used and nothing is signed.
  const executorAddr = receiver.address;
  const executorRole = await arb.EXECUTOR_ROLE();
  if (!(await arb.hasRole(executorRole, executorAddr))) {
    throw new Error(`${executorAddr} does not hold EXECUTOR_ROLE on the deployed contract.`);
  }
  await ethers.provider.send("hardhat_impersonateAccount", [executorAddr]);
  await ethers.provider.send("hardhat_setBalance", [executorAddr, "0x" + ethers.parseEther("10").toString(16)]);
  const executor = await ethers.getSigner(executorAddr);
  console.log(`  executor role    : held by ${executorAddr} ✓`);

  // Manufacture a dislocation between the 0.05% and 0.30% WETH/USDC tiers by
  // pushing size through the 0.30% pool.
  const [, manipulator] = await ethers.getSigners();
  const weth = await ethers.getContractAt(ERC20_ABI, WETH);
  const dump = ethers.parseEther("3000");
  await ethers.provider.send("hardhat_setBalance", [
    manipulator.address,
    "0x" + (dump + ethers.parseEther("1000")).toString(16),
  ]);
  await (await weth.connect(manipulator).deposit({ value: dump })).wait();
  await (await weth.connect(manipulator).approve(V3_ROUTER, ethers.MaxUint256)).wait();
  const router = await ethers.getContractAt(
    [
      "function exactInputSingle((address,address,uint24,address,uint256,uint256,uint160)) payable returns (uint256)",
    ],
    V3_ROUTER
  );
  await (
    await router
      .connect(manipulator)
      .exactInputSingle([WETH, USDC, 3000, manipulator.address, dump, 0, 0])
  ).wait();
  console.log(`  dislocation      : pushed ${ethers.formatEther(dump)} WETH through the 0.30% tier`);

  // Buy cheap in the dislocated 0.30% pool, sell rich in the untouched 0.05%.
  // Borrow from Aave, not Balancer: Balancer's Base vault holds only ~28 WETH
  // (a larger request reverts BAL#528, INSUFFICIENT_FLASH_LOAN_BALANCE), while
  // Aave's Base reserve holds ~18,000. Aave also charges its real premium, so
  // the profit check has to clear a genuine cost rather than a zero fee.
  const borrow = ethers.parseEther("50");
  const params = {
    provider: Provider.AAVE_V3,
    asset: WETH,
    amount: borrow,
    minProfit: 1n,
    profitReceiver: receiver.address,
    deadline: MAX_DEADLINE,
    steps: [v3Step(WETH, USDC, 500), v3Step(USDC, WETH, 3000)],
  };

  const before = await weth.balanceOf(receiver.address);
  console.log(`\n  borrowing        : ${ethers.formatEther(borrow)} WETH`);
  console.log(`  route            : WETH →[0.05%]→ USDC →[0.30%]→ WETH`);

  const tx = await arb.connect(executor).executeArbitrage(params);
  const receipt = await tx.wait();
  const profit = (await weth.balanceOf(receiver.address)) - before;

  const event = receipt.logs
    .filter((l) => l.address.toLowerCase() === address.toLowerCase())
    .map((l) => {
      try {
        return arb.interface.parseLog(l);
      } catch {
        return null;
      }
    })
    .find((p) => p && p.name === "ArbitrageExecuted");
  if (!event) throw new Error("ArbitrageExecuted not emitted.");

  console.log("\n───────────────────────────────────────────────────────────────");
  console.log(`  ✅ THE DEPLOYED CONTRACT EXECUTED. Profit: ${ethers.formatEther(profit)} WETH`);
  console.log(`     → ${receiver.address}`);
  console.log("───────────────────────────────────────────────────────────────");
  console.log(`  gas used         : ${receipt.gasUsed}`);
  console.log(`  logged profit    : ${ethers.formatEther(event.args.profit)} WETH`);
  console.log(`  logged receiver  : ${event.args.profitReceiver}`);

  if (profit <= 0n) throw new Error("Receiver balance did not increase.");
  if (event.args.profit !== profit) throw new Error("Logged profit != balance delta.");
  if (event.args.profitReceiver !== receiver.address) throw new Error("Logged receiver mismatch.");
  if ((await weth.balanceOf(address)) !== 0n) throw new Error("Executor retained funds.");
  console.log(`  executor residual: 0 ✓`);

  console.log("\n  Ran against forked state — nothing broadcast, no real funds moved.");
  console.log("  The dislocation was manufactured; this proves the DEPLOYED bytecode");
  console.log("  executes and pays the named wallet.\n");
}

main().catch((e) => {
  console.error(`\n❌ ${e.message}\n`);
  process.exitCode = 1;
});
