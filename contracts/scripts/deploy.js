/**
 * Deploys FlashLoanArbitrage AND CrossChainArbitrageExecutor to the selected
 * network using config/addresses.js.
 *
 *   npx hardhat run scripts/deploy.js --network arbitrum
 *   npx hardhat run scripts/deploy.js --network polygon
 *
 * Requires PRIVATE_KEY in the environment (see .env.example). The deployer
 * becomes the initial admin/guardian/executor on both contracts; transfer/
 * grant roles to your bot key and a multisig guardian afterwards.
 *
 * CrossChainArbitrageExecutor needs one sibling deployment per chain you
 * bridge between (e.g. run this on both "arbitrum" and "polygon" to get the
 * pair of executors the cross-chain flow needs — see docs/DEPLOYMENT.md).
 * Set SKIP_CROSSCHAIN=1 to deploy only FlashLoanArbitrage, matching this
 * script's pre-existing behavior.
 */
const fs = require("fs");
const path = require("path");
const hre = require("hardhat");
const { forNetwork, ZERO } = require("../config/addresses");

async function main() {
  const net = hre.network.name;
  const { ethers } = hre;
  const [deployer] = await ethers.getSigners();

  const book = forNetwork(net);
  const aavePool = book.aavePool || ZERO;
  const balancerVault = book.balancerVault || ZERO;

  if (aavePool === ZERO && balancerVault === ZERO) {
    throw new Error(
      `Neither aavePool nor balancerVault is set for "${net}" in config/addresses.js — ` +
        `verify and fill in at least one flash-loan provider before deploying.`
    );
  }

  console.log(`\nDeploying FlashLoanArbitrage to ${net} (chainId ${book.chainId})`);
  console.log(`  deployer     : ${deployer.address}`);
  console.log(`  aavePool     : ${aavePool}`);
  console.log(`  balancerVault: ${balancerVault}`);

  const Arb = await ethers.getContractFactory("FlashLoanArbitrage");
  const arb = await Arb.deploy(aavePool, balancerVault, deployer.address);
  await arb.waitForDeployment();
  const address = await arb.getAddress();
  console.log(`\n✅ FlashLoanArbitrage deployed at: ${address}`);

  let crossChainAddress = null;
  if (process.env.SKIP_CROSSCHAIN !== "1") {
    console.log(`\nDeploying CrossChainArbitrageExecutor to ${net}`);
    const XChain = await ethers.getContractFactory("CrossChainArbitrageExecutor");
    const xchain = await XChain.deploy(deployer.address);
    await xchain.waitForDeployment();
    crossChainAddress = await xchain.getAddress();
    console.log(`✅ CrossChainArbitrageExecutor deployed at: ${crossChainAddress}`);
  }

  // Persist a deployment record (git-ignored) for tooling.
  const outDir = path.join(__dirname, "..", "deployments");
  fs.mkdirSync(outDir, { recursive: true });
  const record = {
    network: net,
    chainId: book.chainId,
    address,
    crossChainAddress,
    aavePool,
    balancerVault,
    deployer: deployer.address,
    deployedAt: new Date().toISOString(),
  };
  fs.writeFileSync(
    path.join(outDir, `${net}.json`),
    JSON.stringify(record, null, 2)
  );
  console.log(`\n   record written to deployments/${net}.json`);

  console.log(`\nVerify with:\n  npx hardhat verify --network ${net} ${address} ${aavePool} ${balancerVault} ${deployer.address}`);
  if (crossChainAddress) {
    console.log(`  npx hardhat verify --network ${net} ${crossChainAddress} ${deployer.address}`);
  }
  console.log("");
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
