const { expect } = require("chai");
const { ethers } = require("hardhat");
const { MAX_DEADLINE, v2Step } = require("./helpers");

// The cross-chain executor is inventory-based and NON-atomic across chains.
// These tests exercise each leg independently on a single chain (as it runs in
// production: leg A on the source chain, leg B on the destination chain).
describe("CrossChainArbitrageExecutor (inventory legs)", () => {
  const e = (n, d = 18n) => BigInt(n) * 10n ** d;
  const FEE_BPS = 30n;

  async function deploy() {
    const [admin, bot, other] = await ethers.getSigners();
    const ERC20 = await ethers.getContractFactory("MockERC20");
    const usdc = await ERC20.deploy("USD Coin", "USDC", 6);
    const weth = await ERC20.deploy("Wrapped Ether", "WETH", 18);

    const Pool = await ethers.getContractFactory("MockUniV2");
    const pool = await Pool.deploy(usdc.target, weth.target, FEE_BPS);
    await usdc.mint(pool.target, e(200000, 6n));
    await weth.mint(pool.target, e(100));

    const Bridge = await ethers.getContractFactory("MockBridgeAdapter");
    const bridge = await Bridge.deploy();

    const X = await ethers.getContractFactory("CrossChainArbitrageExecutor");
    const xchain = await X.deploy(admin.address);
    await xchain.grantRole(await xchain.EXECUTOR_ROLE(), bot.address);

    return { admin, bot, other, usdc, weth, pool, bridge, xchain };
  }

  it("source leg swaps inventory then dispatches to the bridge", async () => {
    const f = await deploy();
    // Pre-position WETH inventory on the source executor.
    await f.weth.mint(f.xchain.target, e(1));

    const steps = [v2Step(f.pool.target, f.weth.target, f.usdc.target)];
    await expect(
      f.xchain
        .connect(f.bot)
        .executeSourceLeg(
          steps,
          f.weth.target,
          e(1),
          f.bridge.target,
          f.usdc.target,
          1n,
          8453,
          f.other.address,
          "0x"
        )
    ).to.emit(f.xchain, "SourceLegDispatched");

    // USDC ended up in the bridge adapter (simulating the outbound transfer).
    expect(await f.usdc.balanceOf(f.bridge.target)).to.be.gt(0n);
    expect(await f.usdc.balanceOf(f.xchain.target)).to.equal(0n);
  });

  it("destination leg swaps bridged funds into the target asset", async () => {
    const f = await deploy();
    // Simulate funds arriving from the bridge.
    await f.usdc.mint(f.xchain.target, e(1800, 6n));

    const steps = [v2Step(f.pool.target, f.usdc.target, f.weth.target)];
    await expect(
      f.xchain.connect(f.bot).executeDestinationLeg(steps, f.usdc.target, 0n, 1n)
    ).to.emit(f.xchain, "DestinationLegSettled");
    expect(await f.weth.balanceOf(f.xchain.target)).to.be.gt(0n);
  });

  it("enforces the min-output guard on the destination leg", async () => {
    const f = await deploy();
    await f.usdc.mint(f.xchain.target, e(1800, 6n));
    const steps = [v2Step(f.pool.target, f.usdc.target, f.weth.target)];
    await expect(
      f.xchain.connect(f.bot).executeDestinationLeg(steps, f.usdc.target, 0n, e(1000))
    ).to.be.revertedWithCustomError(f.xchain, "InsufficientLegOutput");
  });

  it("restricts legs to EXECUTOR_ROLE", async () => {
    const f = await deploy();
    await f.usdc.mint(f.xchain.target, e(1800, 6n));
    const steps = [v2Step(f.pool.target, f.usdc.target, f.weth.target)];
    await expect(
      f.xchain.connect(f.other).executeDestinationLeg(steps, f.usdc.target, 0n, 1n)
    ).to.be.revertedWithCustomError(f.xchain, "AccessControlUnauthorizedAccount");
  });
});
