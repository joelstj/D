/**
 * Deploys FlashLoanArbitrage to the selected network using config/addresses.js.
 *
 *   npx hardhat run scripts/deploy.js --network arbitrum
 *
 * Requires PRIVATE_KEY in the environment (see .env.example). The deployer
 * becomes the initial admin/guardian/executor; transfer/grant roles to your
 * bot key and a multisig guardian afterwards.
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

  // Persist a deployment record (git-ignored) for tooling.
  const outDir = path.join(__dirname, "..", "deployments");
  fs.mkdirSync(outDir, { recursive: true });
  const record = {
    network: net,
    chainId: book.chainId,
    address,
    aavePool,
    balancerVault,
    deployer: deployer.address,
    deployedAt: new Date().toISOString(),
  };
  fs.writeFileSync(
    path.join(outDir, `${net}.json`),
    JSON.stringify(record, null, 2)
  );
  console.log(`   record written to deployments/${net}.json`);

  console.log(`\nVerify with:\n  npx hardhat verify --network ${net} ${address} ${aavePool} ${balancerVault} ${deployer.address}\n`);
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
