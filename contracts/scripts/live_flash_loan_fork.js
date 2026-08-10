/**
 * LIVE FLASH-LOAN EXECUTION — one real arbitrage, on live chain state,
 * paying the profit to a real operator wallet.
 *
 *   FORK_RPC_URL=<polygon-or-arbitrum-rpc> \
 *   PROFIT_RECEIVER=0xYourMetaMaskAddress \
 *   npx hardhat run scripts/live_flash_loan_fork.js
 *
 * ## What this actually does
 *
 * Hardhat forks the target chain at its current head block, so every contract
 * this touches is the REAL deployed one, holding its REAL current state:
 *   - a real Aave V3 Pool flash loan, charging the real live premium,
 *   - a real Uniswap V3 `SwapRouter02` swap against real pool liquidity,
 *   - a real UniswapV2-style swap (QuickSwap on Polygon, SushiSwap on
 *     Arbitrum) against real reserves read moments earlier,
 *   - the real `FlashLoanArbitrage` bytecode from this repo.
 * It then asserts the profit landed in `PROFIT_RECEIVER`'s balance.
 *
 * ## What this does NOT do, and why (root CLAUDE.md §2 invariant 3 — binding)
 *
 * Nothing here is broadcast to a public chain. The fork is a local EVM whose
 * state reads come from a real node; writes stay local and are discarded when
 * the process exits. That boundary is deliberate and enforced below: this
 * script REFUSES to run against any network other than the in-process fork, so
 * it cannot be repointed at mainnet by changing a flag. Broadcasting a real
 * arbitrage remains a human-signed MetaMask action — the backend holds no key
 * and this script builds no signer over one.
 *
 * ## Honest reading of the result
 *
 * Like the fork test suites this is modelled on, the profitable case
 * MANUFACTURES its price dislocation (it dumps size into the shallower V2 pool,
 * then captures the gap). So a successful run proves THE PIPELINE IS FULLY
 * OPERATIONAL against live infrastructure and that profit is routed to the
 * address you named — it does NOT prove that risk-free profit is currently
 * sitting on mainnet. Anyone reading this output should take it as
 * "the executor works and pays the right wallet", not as a realised PnL figure.
 */
const hre = require("hardhat");
const { ethers } = hre;

const MAX_DEADLINE = 99999999999n;
const DexType = { UNISWAP_V2: 0, UNISWAP_V3_SINGLE: 1 };
const Provider = { AAVE_V3: 0, BALANCER_V2: 1 };

/**
 * Per-chain live addresses. Every one of these is already used (and therefore
 * exercised against the live chain) by the fork test suites in test/fork/ and
 * is listed in config/addresses.js — none is introduced here. Detection keys
 * off a chain-specific wrapped-native contract rather than the Aave Pool, whose
 * address is shared across several chains.
 */
const CHAINS = [
  {
    name: "Polygon PoS",
    // WMATIC — wraps the native gas token and is the canonical high-liquidity
    // pairing token. Polygon's WETH is a plain bridged ERC20 with no
    // `deposit()`, so it cannot seed the dislocation (see test/fork/PolygonFork).
    wrappedNative: "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270",
    symbol: "WMATIC",
    quote: "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174", // USDC.e
    quoteSymbol: "USDC.e",
    aavePool: "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
    balancerVault: "0xBA12222222228d8Ba445958a75a0704d566BF2C8",
    v3Router: "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45",
    v2Router: "0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff", // QuickSwap
    v3Fee: 3000,
  },
  {
    name: "Arbitrum One",
    wrappedNative: "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1", // WETH
    symbol: "WETH",
    quote: "0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8", // USDC.e
    quoteSymbol: "USDC.e",
    aavePool: "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
    balancerVault: "0xBA12222222228d8Ba445958a75a0704d566BF2C8",
    v3Router: "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45",
    v2Router: "0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506", // SushiSwap
    v3Fee: 500,
  },
];

const ERC20_ABI = [
  "function deposit() payable",
  "function approve(address,uint256) returns (bool)",
  "function balanceOf(address) view returns (uint256)",
  "function decimals() view returns (uint8)",
];

function v3Step(router, tokenIn, tokenOut, fee) {
  return {
    dexType: DexType.UNISWAP_V3_SINGLE,
    router,
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

function v2Step(router, tokenIn, tokenOut) {
  return {
    dexType: DexType.UNISWAP_V2,
    router,
    tokenIn,
    tokenOut,
    poolFee: 0,
    curveI: 0,
    curveJ: 0,
    minOut: 0n,
    data: "0x",
    amountInOffset: 0,
  };
}

/**
 * Resolve the wallet that should receive the profit.
 *
 * `PROFIT_RECEIVER` is the explicit, preferred input. As a convenience for
 * environments that already carry an operator key, the address is otherwise
 * DERIVED from `EXECUTOR_PRIVATE_KEY` — derivation only; the key is never
 * logged, never used to sign anything here, and never leaves this function.
 */
function resolveProfitReceiver(env = process.env) {
  const explicit = env.PROFIT_RECEIVER;
  if (explicit) {
    if (!ethers.isAddress(explicit)) {
      throw new Error(`PROFIT_RECEIVER is not a valid address: ${explicit}`);
    }
    return { address: ethers.getAddress(explicit), source: "PROFIT_RECEIVER" };
  }

  const key = env.EXECUTOR_PRIVATE_KEY || env.PRIVATE_KEY;
  if (key) {
    const normalised = key.trim().startsWith("0x") ? key.trim() : `0x${key.trim()}`;
    return {
      address: new ethers.Wallet(normalised).address,
      source: "derived from EXECUTOR_PRIVATE_KEY (address only; key never used to sign)",
    };
  }

  throw new Error(
    "No profit receiver. Set PROFIT_RECEIVER=0xYourMetaMaskAddress (or provide EXECUTOR_PRIVATE_KEY to derive it)."
  );
}

/**
 * Refuse to run anywhere except the in-process fork.
 *
 * This is the guard that keeps a script which executes a real arbitrage from
 * ever reaching a public chain, so it is written as a pure function of
 * (networkName, env) and unit-tested directly — see test/LiveForkScript.test.js.
 * `hardhat` is the only network whose writes stay local; every other entry in
 * hardhat.config.js is a real RPC that would broadcast.
 */
function assertForkOnly(networkName, env) {
  if (networkName !== "hardhat") {
    throw new Error(
      `Refusing to run on network "${networkName}". This script executes a real arbitrage and ` +
        `must only ever run against the in-process fork (root CLAUDE.md §2 invariant 3: the loop ` +
        `never broadcasts). Run it without --network, with FORK_RPC_URL set.`
    );
  }
  if (!env.FORK_RPC_URL) {
    throw new Error("FORK_RPC_URL is not set — without it Hardhat starts a clean chain with no live pools.");
  }
}

async function detectChain() {
  for (const chain of CHAINS) {
    if ((await ethers.provider.getCode(chain.wrappedNative)) !== "0x") return chain;
  }
  throw new Error(
    `FORK_RPC_URL does not point at a supported chain. Supported: ${CHAINS.map((c) => c.name).join(", ")}.`
  );
}

async function main() {
  assertForkOnly(hre.network.name, process.env);

  const receiver = resolveProfitReceiver(process.env);
  const chain = await detectChain();
  const block = await ethers.provider.getBlockNumber();

  console.log("\n─────────────────────────────────────────────────────────────");
  console.log(" LIVE FLASH-LOAN EXECUTION (forked live state — not broadcast)");
  console.log("─────────────────────────────────────────────────────────────");
  console.log(`  chain            : ${chain.name}`);
  console.log(`  forked at block  : ${block}`);
  console.log(`  profit receiver  : ${receiver.address}`);
  console.log(`  receiver source  : ${receiver.source}`);

  const [deployer, bot, manipulator] = await ethers.getSigners();

  // 1) Deploy this repo's real executor onto the fork.
  const Arb = await ethers.getContractFactory("FlashLoanArbitrage");
  const arb = await Arb.deploy(chain.aavePool, chain.balancerVault, deployer.address);
  await arb.waitForDeployment();
  await (await arb.grantRole(await arb.EXECUTOR_ROLE(), bot.address)).wait();
  console.log(`\n  executor deployed: ${await arb.getAddress()}`);

  // 2) Read the LIVE flash-loan premium straight off the real Aave Pool.
  const premiumBps = await arb.aavePremiumBps();
  console.log(`  live Aave premium: ${premiumBps} bps`);

  // 3) Find the real V2 pair and read its real reserves.
  const v2Router = await ethers.getContractAt(["function factory() view returns (address)"], chain.v2Router);
  const factory = await ethers.getContractAt(
    ["function getPair(address,address) view returns (address)"],
    await v2Router.factory()
  );
  const pairAddr = await factory.getPair(chain.wrappedNative, chain.quote);
  if (pairAddr === ethers.ZeroAddress) throw new Error("No live V2 pair for this token pair.");

  const pair = await ethers.getContractAt(
    ["function getReserves() view returns (uint112,uint112,uint32)", "function token0() view returns (address)"],
    pairAddr
  );
  const [r0, r1] = await pair.getReserves();
  const token0 = await pair.token0();
  const nativeReserve = token0.toLowerCase() === chain.wrappedNative.toLowerCase() ? r0 : r1;
  console.log(`  live V2 pair     : ${pairAddr}`);
  console.log(`  live reserve     : ${ethers.formatEther(nativeReserve)} ${chain.symbol}`);

  // 4) Manufacture the dislocation. See the header: this is what makes the run
  //    deterministic, and it is why the result proves operability rather than
  //    the presence of free mainnet profit.
  const wrapped = await ethers.getContractAt(ERC20_ABI, chain.wrappedNative);
  const dump = (nativeReserve * 50n) / 100n;
  const wrapAmount = dump + ethers.parseEther("1000");
  await ethers.provider.send("hardhat_setBalance", [
    manipulator.address,
    "0x" + (wrapAmount + ethers.parseEther("1000")).toString(16),
  ]);
  await (await wrapped.connect(manipulator).deposit({ value: wrapAmount })).wait();
  await (await wrapped.connect(manipulator).approve(chain.v2Router, ethers.MaxUint256)).wait();
  const swapRouter = await ethers.getContractAt(
    ["function swapExactTokensForTokens(uint256,uint256,address[],address,uint256) returns (uint256[])"],
    chain.v2Router
  );
  await (
    await swapRouter
      .connect(manipulator)
      .swapExactTokensForTokens(dump, 0, [chain.wrappedNative, chain.quote], manipulator.address, MAX_DEADLINE)
  ).wait();
  console.log(`  dislocation      : dumped ${ethers.formatEther(dump)} ${chain.symbol} into the live V2 pool`);

  // 5) Execute ONE real flash-loan arbitrage, paying profit to the operator wallet.
  const borrow = nativeReserve / 1000n; // 0.1% of live depth
  const minProfit = ethers.parseEther("0.01");
  const params = {
    provider: Provider.AAVE_V3,
    asset: chain.wrappedNative,
    amount: borrow,
    minProfit,
    profitReceiver: receiver.address,
    deadline: MAX_DEADLINE,
    steps: [
      v3Step(chain.v3Router, chain.wrappedNative, chain.quote, chain.v3Fee),
      v2Step(chain.v2Router, chain.quote, chain.wrappedNative),
    ],
  };

  const before = await wrapped.balanceOf(receiver.address);
  console.log(`\n  borrowing        : ${ethers.formatEther(borrow)} ${chain.symbol} (real Aave V3 flash loan)`);
  console.log(`  route            : ${chain.symbol} →[UniV3 ${chain.v3Fee / 10000}%]→ ${chain.quoteSymbol} →[V2]→ ${chain.symbol}`);
  console.log(`  receiver balance : ${ethers.formatEther(before)} ${chain.symbol} (before)`);

  const tx = await arb.connect(bot).executeArbitrage(params);
  const receipt = await tx.wait();
  const after = await wrapped.balanceOf(receiver.address);
  const profit = after - before;

  // 6) Verify against the emitted log, not just the balance.
  const event = receipt.logs
    .filter((l) => l.address === arb.target)
    .map((l) => {
      try {
        return arb.interface.parseLog(l);
      } catch {
        return null;
      }
    })
    .find((p) => p && p.name === "ArbitrageExecuted");

  if (!event) throw new Error("ArbitrageExecuted was not emitted — the arbitrage did not complete.");

  console.log(`  receiver balance : ${ethers.formatEther(after)} ${chain.symbol} (after)`);
  console.log("\n─────────────────────────────────────────────────────────────");
  console.log(`  ✅ PROFIT DEPOSITED: ${ethers.formatEther(profit)} ${chain.symbol}`);
  console.log(`     → ${receiver.address}`);
  console.log("─────────────────────────────────────────────────────────────");
  console.log(`  tx hash (fork)   : ${receipt.hash}`);
  console.log(`  gas used         : ${receipt.gasUsed}`);
  console.log(`  amount borrowed  : ${ethers.formatEther(event.args.amountBorrowed)} ${chain.symbol}`);
  console.log(`  amount owed      : ${ethers.formatEther(event.args.amountOwed)} ${chain.symbol} (loan + live premium)`);
  console.log(`  logged profit    : ${ethers.formatEther(event.args.profit)} ${chain.symbol}`);
  console.log(`  logged receiver  : ${event.args.profitReceiver}`);

  // Hard assertions — a misreported success here would be worse than a failure.
  if (profit <= 0n) throw new Error("Receiver balance did not increase.");
  if (event.args.profit !== profit) {
    throw new Error(`Logged profit ${event.args.profit} != balance delta ${profit}.`);
  }
  if (event.args.profitReceiver !== receiver.address) {
    throw new Error(`Logged receiver ${event.args.profitReceiver} != requested ${receiver.address}.`);
  }
  const residual = await wrapped.balanceOf(arb.target);
  if (residual !== 0n) throw new Error(`Executor retained ${residual} — it must keep nothing.`);
  console.log(`  executor residual: 0 (retains nothing)`);

  console.log("\n  NOTE: executed against forked live state. Nothing was broadcast to");
  console.log("        a public chain, and the dislocation above was manufactured, so");
  console.log("        this proves the pipeline works and pays the named wallet — it");
  console.log("        is not a realised mainnet profit.\n");
}

// Only auto-run when invoked as a script; importing it (for the unit tests
// below) must not fire a live execution.
if (require.main === module) {
  main().catch((err) => {
    console.error(`\n❌ ${err.message}\n`);
    process.exitCode = 1;
  });
}

module.exports = { assertForkOnly, resolveProfitReceiver, CHAINS };
