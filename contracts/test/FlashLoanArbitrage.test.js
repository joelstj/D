const { expect } = require("chai");
const { ethers } = require("hardhat");
const { DexType, Provider, MAX_DEADLINE, v2Step, getAmountOut } = require("./helpers");

// Offline unit tests: a self-contained constant-product world (mock tokens,
// pools, Aave pool and Balancer vault) proving the full atomic flash-loan
// arbitrage mechanics end to end — no RPC or fork required.
describe("FlashLoanArbitrage (offline mechanics)", () => {
  const e = (n, d = 18n) => BigInt(n) * 10n ** d;
  const AAVE_PREMIUM_BPS = 5n; // 0.05%
  const FEE_BPS = 30n; // 0.30% pools

  async function deploy() {
    const [admin, bot, receiver, attacker] = await ethers.getSigners();

    const ERC20 = await ethers.getContractFactory("MockERC20");
    const usdc = await ERC20.deploy("USD Coin", "USDC", 6);
    const weth = await ERC20.deploy("Wrapped Ether", "WETH", 18);

    const Pool = await ethers.getContractFactory("MockUniV2");
    // Cheap-WETH pool (1800 USDC/WETH) and expensive-WETH pool (2200 USDC/WETH).
    const poolCheap = await Pool.deploy(await usdc.getAddress(), await weth.getAddress(), FEE_BPS);
    const poolRich = await Pool.deploy(await usdc.getAddress(), await weth.getAddress(), FEE_BPS);

    await usdc.mint(await poolCheap.getAddress(), e(180000, 6n));
    await weth.mint(await poolCheap.getAddress(), e(100));
    await usdc.mint(await poolRich.getAddress(), e(220000, 6n));
    await weth.mint(await poolRich.getAddress(), e(100));

    const Aave = await ethers.getContractFactory("MockAavePool");
    const aave = await Aave.deploy(AAVE_PREMIUM_BPS);
    const Bal = await ethers.getContractFactory("MockBalancerVault");
    const balancer = await Bal.deploy();

    // Fund the flash providers with lendable USDC.
    await usdc.mint(await aave.getAddress(), e(5_000_000, 6n));
    await usdc.mint(await balancer.getAddress(), e(5_000_000, 6n));

    const Arb = await ethers.getContractFactory("FlashLoanArbitrage");
    const arb = await Arb.deploy(
      await aave.getAddress(),
      await balancer.getAddress(),
      admin.address
    );
    await arb.grantRole(await arb.EXECUTOR_ROLE(), bot.address);

    return { admin, bot, receiver, attacker, usdc, weth, poolCheap, poolRich, aave, balancer, arb };
  }

  function twoHopParams(f, provider, amount, minProfit = 0n) {
    const usdc = f.usdc.target;
    const weth = f.weth.target;
    return {
      provider,
      asset: usdc,
      amount,
      minProfit,
      profitReceiver: f.receiver.address,
      deadline: MAX_DEADLINE,
      steps: [
        v2Step(f.poolCheap.target, usdc, weth), // buy WETH cheap
        v2Step(f.poolRich.target, weth, usdc), // sell WETH rich
      ],
    };
  }

  it("quotes a positive optimal loan size and expected profit", async () => {
    const f = await deploy();
    const [amountIn, expectedProfit] = await f.arb.quoteOptimalTwoHopV2(
      f.poolCheap.target,
      f.poolRich.target,
      f.usdc.target,
      FEE_BPS,
      FEE_BPS
    );
    expect(amountIn).to.be.gt(0n);
    expect(expectedProfit).to.be.gt(0n);
  });

  // quoteOptimalTwoHopV2 resolves each pair via _pairInfo, which branches on
  // whether the pair's token0() is the borrowed asset or the intermediate.
  // The test above borrows USDC; this one borrows WETH against the same pools
  // (with pairBuy/pairSell swapped so the trade is still profitable) so the
  // opposite branch — and its Yul token0()/token1()/getReserves() reads — is
  // exercised too.
  it("quotes correctly when borrowing the other token of the pair (opposite _pairInfo branch)", async () => {
    const f = await deploy();
    const rWeth = e(100);
    const rUsdcCheap = e(180000, 6n);
    const rUsdcRich = e(220000, 6n);

    const [amountIn, expectedProfit] = await f.arb.quoteOptimalTwoHopV2(
      f.poolRich.target, // sell WETH where it's dear (2200 USDC/WETH)
      f.poolCheap.target, // buy WETH back where it's cheap (1800 USDC/WETH)
      f.weth.target,
      FEE_BPS,
      FEE_BPS
    );
    expect(amountIn).to.be.gt(0n);
    expect(expectedProfit).to.be.gt(0n);

    const out1 = getAmountOut(amountIn, rWeth, rUsdcRich, FEE_BPS);
    const out2 = getAmountOut(out1, rUsdcCheap, rWeth, FEE_BPS);
    expect(expectedProfit).to.equal(out2 - amountIn);
  });

  it("executes a profitable 2-hop arb via Aave V3 and forwards exact profit", async () => {
    const f = await deploy();
    const amount = e(10000, 6n);

    // Predict the outcome using the same constant-product math the pools use.
    const rInA = e(180000, 6n);
    const rOutA = e(100);
    const rInB = e(100);
    const rOutB = e(220000, 6n);
    const out1 = getAmountOut(amount, rInA, rOutA, FEE_BPS);
    const generated = getAmountOut(out1, rInB, rOutB, FEE_BPS);
    const premium = (amount * AAVE_PREMIUM_BPS) / 10000n;
    const owed = amount + premium;
    const expectedProfit = generated - owed;
    expect(expectedProfit).to.be.gt(0n);

    const before = await f.usdc.balanceOf(f.receiver.address);
    await expect(f.arb.connect(f.bot).executeArbitrage(twoHopParams(f, Provider.AAVE_V3, amount)))
      .to.emit(f.arb, "ArbitrageExecuted");
    const after = await f.usdc.balanceOf(f.receiver.address);

    expect(after - before).to.equal(expectedProfit);
    // The engine must never retain the borrowed asset.
    expect(await f.usdc.balanceOf(f.arb.target)).to.equal(0n);
  });

  // Regression for the dashboard "profit straight to the connected wallet" path:
  // when profitReceiver is left as address(0) (the dashboard's default when the
  // operator has not named a different recipient), the contract must pay the
  // whole profit to msg.sender — the wallet that SIGNED the tx (the connected
  // MetaMask account, which carries EXECUTOR_ROLE) — and retain nothing itself.
  it("profitReceiver=0 deposits profit into the tx signer's wallet (connected-wallet default), zero residual", async () => {
    const f = await deploy();
    const amount = e(10000, 6n);

    const out1 = getAmountOut(amount, e(180000, 6n), e(100), FEE_BPS);
    const generated = getAmountOut(out1, e(100), e(220000, 6n), FEE_BPS);
    const owed = amount + (amount * AAVE_PREMIUM_BPS) / 10000n;
    const expectedProfit = generated - owed;
    expect(expectedProfit).to.be.gt(0n);

    const params = {
      ...twoHopParams(f, Provider.AAVE_V3, amount),
      profitReceiver: ethers.ZeroAddress, // unset => pay the signer
    };

    const signerBefore = await f.usdc.balanceOf(f.bot.address);
    const receiverBefore = await f.usdc.balanceOf(f.receiver.address);
    await f.arb.connect(f.bot).executeArbitrage(params);

    // Signer (connected wallet) got the exact profit; the unrelated fixture
    // receiver got nothing; the contract kept nothing.
    expect((await f.usdc.balanceOf(f.bot.address)) - signerBefore).to.equal(expectedProfit);
    expect(await f.usdc.balanceOf(f.receiver.address)).to.equal(receiverBefore);
    expect(await f.usdc.balanceOf(f.arb.target)).to.equal(0n);
  });

  it("executes a profitable 2-hop arb via Balancer V2 (0 fee)", async () => {
    const f = await deploy();
    const amount = e(10000, 6n);
    const out1 = getAmountOut(amount, e(180000, 6n), e(100), FEE_BPS);
    const generated = getAmountOut(out1, e(100), e(220000, 6n), FEE_BPS);
    const expectedProfit = generated - amount; // 0 balancer fee

    const before = await f.usdc.balanceOf(f.receiver.address);
    await f.arb.connect(f.bot).executeArbitrage(twoHopParams(f, Provider.BALANCER_V2, amount));
    const after = await f.usdc.balanceOf(f.receiver.address);
    expect(after - before).to.equal(expectedProfit);
  });

  it("executes a profitable 3-hop triangular arb", async () => {
    const [admin, bot, receiver] = await ethers.getSigners();
    const ERC20 = await ethers.getContractFactory("MockERC20");
    const A = await ERC20.deploy("Token A", "A", 18);
    const B = await ERC20.deploy("Token B", "B", 18);
    const C = await ERC20.deploy("Token C", "C", 18);

    const Pool = await ethers.getContractFactory("MockUniV2");
    const ab = await Pool.deploy(A.target, B.target, FEE_BPS);
    const bc = await Pool.deploy(B.target, C.target, FEE_BPS);
    const ca = await Pool.deploy(C.target, A.target, FEE_BPS);

    await A.mint(ab.target, e(1000));
    await B.mint(ab.target, e(1000));
    await B.mint(bc.target, e(1000));
    await C.mint(bc.target, e(1000));
    await C.mint(ca.target, e(1000));
    await A.mint(ca.target, e(1300)); // cycle mispricing: C is under-priced in A

    const Bal = await ethers.getContractFactory("MockBalancerVault");
    const balancer = await Bal.deploy();
    await A.mint(balancer.target, e(100000));

    const Arb = await ethers.getContractFactory("FlashLoanArbitrage");
    const arb = await Arb.deploy(ethers.ZeroAddress, balancer.target, admin.address);
    await arb.grantRole(await arb.EXECUTOR_ROLE(), bot.address);

    const amount = e(10);
    const params = {
      provider: Provider.BALANCER_V2,
      asset: A.target,
      amount,
      minProfit: 0n,
      profitReceiver: receiver.address,
      deadline: MAX_DEADLINE,
      steps: [
        v2Step(ab.target, A.target, B.target),
        v2Step(bc.target, B.target, C.target),
        v2Step(ca.target, C.target, A.target),
      ],
    };

    const before = await A.balanceOf(receiver.address);
    await expect(arb.connect(bot).executeArbitrage(params)).to.emit(arb, "ArbitrageExecuted");
    const profit = (await A.balanceOf(receiver.address)) - before;
    expect(profit).to.be.gt(0n);
  });

  it("reverts with InsufficientProfit when minProfit is unreachable", async () => {
    const f = await deploy();
    const amount = e(10000, 6n);
    const params = twoHopParams(f, Provider.AAVE_V3, amount, e(1_000_000, 6n));
    await expect(
      f.arb.connect(f.bot).executeArbitrage(params)
    ).to.be.revertedWithCustomError(f.arb, "InsufficientProfit");
  });

  it("reverts an unprofitable route atomically (equal-price pools)", async () => {
    const f = await deploy();
    // Point both hops at the same pool => round-trip loses the fees, no profit.
    const usdc = f.usdc.target;
    const weth = f.weth.target;
    const params = {
      provider: Provider.BALANCER_V2,
      asset: usdc,
      amount: e(10000, 6n),
      minProfit: 1n,
      profitReceiver: f.receiver.address,
      deadline: MAX_DEADLINE,
      steps: [v2Step(f.poolCheap.target, usdc, weth), v2Step(f.poolCheap.target, weth, usdc)],
    };
    await expect(
      f.arb.connect(f.bot).executeArbitrage(params)
    ).to.be.revertedWithCustomError(f.arb, "InsufficientProfit");
  });

  it("enforces EXECUTOR_ROLE on executeArbitrage", async () => {
    const f = await deploy();
    const params = twoHopParams(f, Provider.AAVE_V3, e(10000, 6n));
    await expect(
      f.arb.connect(f.attacker).executeArbitrage(params)
    ).to.be.revertedWithCustomError(f.arb, "AccessControlUnauthorizedAccount");
  });

  it("rejects a route that does not start and end in the borrowed asset", async () => {
    const f = await deploy();
    const params = twoHopParams(f, Provider.AAVE_V3, e(10000, 6n));
    params.steps[1].tokenOut = f.weth.target; // ends in WETH, not USDC
    await expect(
      f.arb.connect(f.bot).executeArbitrage(params)
    ).to.be.revertedWithCustomError(f.arb, "RouteAssetMismatch");
  });

  it("rejects routes shorter than two hops", async () => {
    const f = await deploy();
    const params = twoHopParams(f, Provider.AAVE_V3, e(10000, 6n));
    params.steps = [params.steps[0]];
    await expect(
      f.arb.connect(f.bot).executeArbitrage(params)
    ).to.be.revertedWithCustomError(f.arb, "InvalidRoute");
  });

  it("blocks an unsolicited Aave callback (griefer-initiated flash loan)", async () => {
    const f = await deploy();
    // Attacker asks the pool to flash-loan straight into our contract; initiator
    // will be the attacker, not our contract => callback must reject.
    await expect(
      f.aave
        .connect(f.attacker)
        .flashLoanSimple(f.arb.target, f.usdc.target, e(1000, 6n), "0x", 0)
    ).to.be.revertedWithCustomError(f.arb, "UnexpectedInitiator");
  });

  it("blocks a direct call to receiveFlashLoan (not armed / wrong caller)", async () => {
    const f = await deploy();
    await expect(
      f.arb
        .connect(f.attacker)
        .receiveFlashLoan([f.usdc.target], [e(1000, 6n)], [0n], "0x")
    ).to.be.revertedWithCustomError(f.arb, "UnexpectedCaller");
  });

  it("blocks a direct call to executeOperation", async () => {
    const f = await deploy();
    await expect(
      f.arb
        .connect(f.attacker)
        .executeOperation(f.usdc.target, e(1000, 6n), 0n, f.arb.target, "0x")
    ).to.be.revertedWithCustomError(f.arb, "UnexpectedCaller");
  });

  it("honours pause / unpause", async () => {
    const f = await deploy();
    await f.arb.connect(f.admin).pause();
    await expect(
      f.arb.connect(f.bot).executeArbitrage(twoHopParams(f, Provider.AAVE_V3, e(10000, 6n)))
    ).to.be.revertedWithCustomError(f.arb, "EnforcedPause");
    await f.arb.connect(f.admin).unpause();
    await expect(
      f.arb.connect(f.bot).executeArbitrage(twoHopParams(f, Provider.AAVE_V3, e(10000, 6n)))
    ).to.emit(f.arb, "ArbitrageExecuted");
  });

  it("lets a guardian rescue tokens but not a stranger", async () => {
    const f = await deploy();
    await f.usdc.mint(f.arb.target, e(123, 6n));
    await expect(
      f.arb.connect(f.attacker).rescueTokens(f.usdc.target, f.attacker.address, 0n)
    ).to.be.revertedWithCustomError(f.arb, "AccessControlUnauthorizedAccount");
    await f.arb.connect(f.admin).rescueTokens(f.usdc.target, f.admin.address, 0n);
    expect(await f.usdc.balanceOf(f.admin.address)).to.equal(e(123, 6n));
  });

  // --------------------------------------------------------------------- //
  //   GENERIC-hop router allowlist                                        //
  // --------------------------------------------------------------------- //

  function genericStep(router, tokenIn, tokenOut, data, amountInOffset = 0n) {
    return {
      dexType: DexType.GENERIC,
      router,
      tokenIn,
      tokenOut,
      poolFee: 0,
      curveI: 0,
      curveJ: 0,
      minOut: 0n,
      data,
      amountInOffset,
    };
  }

  it("only a guardian can update the GENERIC router allowlist, not the executor bot", async () => {
    const f = await deploy();
    await expect(
      f.arb.connect(f.attacker).setGenericRouterAllowed(f.poolRich.target, true)
    ).to.be.revertedWithCustomError(f.arb, "AccessControlUnauthorizedAccount");
    // The bot holds EXECUTOR_ROLE (can pick routes) but not GUARDIAN_ROLE — it
    // must not also be able to expand what GENERIC is allowed to call.
    await expect(
      f.arb.connect(f.bot).setGenericRouterAllowed(f.poolRich.target, true)
    ).to.be.revertedWithCustomError(f.arb, "AccessControlUnauthorizedAccount");

    await expect(f.arb.connect(f.admin).setGenericRouterAllowed(f.poolRich.target, true))
      .to.emit(f.arb, "GenericRouterAllowlistUpdated")
      .withArgs(f.poolRich.target, true);
    expect(await f.arb.allowedGenericRouters(f.poolRich.target)).to.equal(true);

    await f.arb.connect(f.admin).setGenericRouterAllowed(f.poolRich.target, false);
    expect(await f.arb.allowedGenericRouters(f.poolRich.target)).to.equal(false);
  });

  it("rejects a GENERIC hop whose router isn't allowlisted", async () => {
    const f = await deploy();
    const path = [f.weth.target, f.usdc.target];
    const data = f.poolRich.interface.encodeFunctionData("swapExactTokensForTokens", [
      0n,
      0n,
      path,
      f.arb.target,
      MAX_DEADLINE,
    ]);
    const params = twoHopParams(f, Provider.AAVE_V3, e(10000, 6n));
    params.steps[1] = genericStep(f.poolRich.target, f.weth.target, f.usdc.target, data, 4n);

    await expect(f.arb.connect(f.bot).executeArbitrage(params))
      .to.be.revertedWithCustomError(f.arb, "GenericRouterNotAllowed")
      .withArgs(f.poolRich.target);
  });

  it("executes a GENERIC hop successfully once its router is allowlisted (regression check)", async () => {
    const f = await deploy();
    await f.arb.connect(f.admin).setGenericRouterAllowed(f.poolRich.target, true);

    const amount = e(10000, 6n);
    const out1 = getAmountOut(amount, e(180000, 6n), e(100), FEE_BPS);
    const generated = getAmountOut(out1, e(100), e(220000, 6n), FEE_BPS);
    const premium = (amount * AAVE_PREMIUM_BPS) / 10000n;
    const expectedProfit = generated - (amount + premium);
    expect(expectedProfit).to.be.gt(0n);

    // amountInOffset=4 patches the first param (amountIn) with the real,
    // dynamically-sized running balance at call time — the amountIn passed
    // to encodeFunctionData below is only a placeholder.
    const path = [f.weth.target, f.usdc.target];
    const data = f.poolRich.interface.encodeFunctionData("swapExactTokensForTokens", [
      0n,
      0n,
      path,
      f.arb.target,
      MAX_DEADLINE,
    ]);
    const params = twoHopParams(f, Provider.AAVE_V3, amount);
    params.steps[1] = genericStep(f.poolRich.target, f.weth.target, f.usdc.target, data, 4n);

    const before = await f.usdc.balanceOf(f.receiver.address);
    await expect(f.arb.connect(f.bot).executeArbitrage(params)).to.emit(f.arb, "ArbitrageExecuted");
    const after = await f.usdc.balanceOf(f.receiver.address);
    expect(after - before).to.equal(expectedProfit);
    expect(await f.usdc.balanceOf(f.arb.target)).to.equal(0n);
  });

  it("prevents an unallowlisted GENERIC router from draining an unrelated held token", async () => {
    const f = await deploy();
    // The engine happens to hold idle USDC already — e.g. dust from a prior
    // trade — completely unrelated to this attempted arbitrage's own route.
    const heldAmount = e(50000, 6n);
    await f.usdc.mint(f.arb.target, heldAmount);

    const maliciousData = f.usdc.interface.encodeFunctionData("transfer", [
      f.attacker.address,
      heldAmount,
    ]);
    const params = twoHopParams(f, Provider.AAVE_V3, e(10000, 6n));
    // Declared tokenIn/tokenOut satisfy the outer route-asset check; the real
    // calldata does something else entirely (the exact gap this closes).
    params.steps[1] = genericStep(f.usdc.target, f.weth.target, f.usdc.target, maliciousData);

    await expect(f.arb.connect(f.bot).executeArbitrage(params))
      .to.be.revertedWithCustomError(f.arb, "GenericRouterNotAllowed")
      .withArgs(f.usdc.target);

    expect(await f.usdc.balanceOf(f.attacker.address)).to.equal(0n);
    expect(await f.usdc.balanceOf(f.arb.target)).to.equal(heldAmount);
  });

  it("reverts a discontinuous route that would vacuum a held non-asset token (RouteNotContiguous)", async () => {
    const f = await deploy();
    // The contract holds a parked, unrelated non-asset token (airdrop / dust /
    // mis-send) — exactly what rescueTokens exists to sweep, so a non-zero
    // holding is realistic.
    const ERC20 = await ethers.getContractFactory("MockERC20");
    const wbtc = await ERC20.deploy("Wrapped BTC", "WBTC", 8);
    const heldWbtc = e(1, 8n); // 1 WBTC
    await wbtc.mint(f.arb.target, heldWbtc);

    // A pool that could sell that held WBTC back into the borrowed asset.
    const Pool = await ethers.getContractFactory("MockUniV2");
    const poolWbtc = await Pool.deploy(f.usdc.target, wbtc.target, FEE_BPS);
    await f.usdc.mint(poolWbtc.target, e(200000, 6n));
    await wbtc.mint(poolWbtc.target, e(3, 8n));

    // Route: USDC -> WETH (real), then a DISCONTINUOUS hop whose tokenIn is WBTC
    // (NOT the WETH the prior hop produced). First/last legs still start and end
    // in USDC, so the route-asset check passes — only the contiguity check
    // catches this. Without it, `_runRoute`'s live-balance cap would swap the
    // entire held WBTC into USDC and `_settle` would pay it to profitReceiver.
    const params = twoHopParams(f, Provider.AAVE_V3, e(10000, 6n));
    params.steps[1] = v2Step(poolWbtc.target, wbtc.target, f.usdc.target);

    await expect(f.arb.connect(f.bot).executeArbitrage(params))
      .to.be.revertedWithCustomError(f.arb, "RouteNotContiguous")
      .withArgs(1);

    // Held WBTC untouched; nothing reached the attacker; no flash loan taken.
    expect(await wbtc.balanceOf(f.arb.target)).to.equal(heldWbtc);
    expect(await wbtc.balanceOf(f.attacker.address)).to.equal(0n);
  });

  it("still executes a legitimate contiguous multi-hop route (contiguity guard is not over-broad)", async () => {
    const f = await deploy();
    const params = twoHopParams(f, Provider.AAVE_V3, e(10000, 6n));
    await expect(f.arb.connect(f.bot).executeArbitrage(params)).to.emit(
      f.arb,
      "ArbitrageExecuted"
    );
  });

  it("reports the Aave premium in bps", async () => {
    const f = await deploy();
    expect(await f.arb.aavePremiumBps()).to.equal(AAVE_PREMIUM_BPS);
  });

  it("borrowing the quoted optimal size is profitable and near-locally-optimal", async () => {
    const f = await deploy();
    const [opt] = await f.arb.quoteOptimalTwoHopV2(
      f.poolCheap.target,
      f.poolRich.target,
      f.usdc.target,
      FEE_BPS,
      FEE_BPS
    );

    const profitAt = (amount) => {
      const out1 = getAmountOut(amount, e(180000, 6n), e(100), FEE_BPS);
      const generated = getAmountOut(out1, e(100), e(220000, 6n), FEE_BPS);
      const owed = amount + (amount * AAVE_PREMIUM_BPS) / 10000n;
      return generated - owed;
    };

    const pOpt = profitAt(opt);
    expect(pOpt).to.be.gt(0n);
    // Perturbing the size by ±20% should not beat the quoted optimum.
    expect(pOpt).to.be.gte(profitAt((opt * 80n) / 100n));
    expect(pOpt).to.be.gte(profitAt((opt * 120n) / 100n));
  });
});
