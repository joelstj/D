/**
 * READ-ONLY pre-flight for a real deployment.
 *
 * Estimates the deploy gas against live chain state and reports whether the
 * deployer can afford it, WITHOUT sending anything. Issues only `eth_estimateGas`,
 * `eth_getBalance`, `eth_gasPrice` and `eth_getCode` — no transaction is signed
 * or broadcast. Run this before scripts/deploy.js on any real network.
 *
 *   PRIVATE_KEY=... npx hardhat run scripts/preflight_deploy.js --network base
 */
const hre = require("hardhat");
const { ethers } = hre;
const { forNetwork, ZERO } = require("../config/addresses");

async function main() {
  const net = hre.network.name;
  if (net === "hardhat") throw new Error("Point this at a real network to be meaningful (--network base).");

  const [deployer] = await ethers.getSigners();
  const book = forNetwork(net);
  const aavePool = book.aavePool || ZERO;
  const balancerVault = book.balancerVault || ZERO;

  console.log(`\nPre-flight for ${net} (chainId ${book.chainId})`);
  console.log(`  deployer      : ${deployer.address}`);
  console.log(`  aavePool      : ${aavePool}`);
  console.log(`  balancerVault : ${balancerVault}`);

  // Both provider addresses must actually be contracts on this chain.
  for (const [label, addr] of [
    ["aavePool", aavePool],
    ["balancerVault", balancerVault],
  ]) {
    if (addr === ZERO) {
      console.log(`  ${label.padEnd(14)}: not configured (skipped)`);
      continue;
    }
    const code = await ethers.provider.getCode(addr);
    if (code === "0x") throw new Error(`${label} ${addr} has NO CODE on ${net} — refusing to deploy against it.`);
    console.log(`  ${label.padEnd(14)}: ${(code.length / 2 - 1).toLocaleString()} bytes of code ✓`);
  }

  const results = [];
  for (const name of ["FlashLoanArbitrage", "CrossChainArbitrageExecutor"]) {
    const factory = await ethers.getContractFactory(name);
    const args = name === "FlashLoanArbitrage" ? [aavePool, balancerVault, deployer.address] : [deployer.address];
    const tx = await factory.getDeployTransaction(...args);
    const gas = await ethers.provider.estimateGas({ ...tx, from: deployer.address });
    results.push({ name, gas });
  }

  const feeData = await ethers.provider.getFeeData();
  const gasPrice = feeData.maxFeePerGas ?? feeData.gasPrice;
  const balance = await ethers.provider.getBalance(deployer.address);

  console.log(`\n  gas price     : ${ethers.formatUnits(gasPrice, "gwei")} gwei`);
  console.log(`  balance       : ${ethers.formatEther(balance)} ETH`);

  let total = 0n;
  for (const r of results) {
    const cost = r.gas * gasPrice;
    total += cost;
    console.log(`\n  ${r.name}`);
    console.log(`    gas         : ${r.gas.toLocaleString()}`);
    console.log(`    cost        : ${ethers.formatEther(cost)} ETH`);
  }

  console.log(`\n  TOTAL cost    : ${ethers.formatEther(total)} ETH`);
  console.log(`  remaining     : ${ethers.formatEther(balance - total)} ETH`);
  console.log(`  affordable    : ${balance > total * 2n ? "YES (>2x headroom)" : balance > total ? "TIGHT" : "NO"}`);
  console.log(`\n  Nothing was sent. This was estimate-only.\n`);
}

main().catch((e) => {
  console.error(`\n❌ ${e.message}\n`);
  process.exitCode = 1;
});
