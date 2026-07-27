require("@nomicfoundation/hardhat-toolbox");

// Consolidated config: the whole product is driven by ONE master `.env` at the
// repo root. An optional local `contracts/.env` overrides it, so load the local
// file first (dotenv won't clobber an already-set key) and let the master fill
// the gaps; real environment variables override both. Keeping deploy secrets
// (PRIVATE_KEY) in a local `contracts/.env` instead of the shared master is
// supported this way.
const path = require("path");
require("dotenv").config({ path: path.resolve(__dirname, ".env") }); // contracts-local override (wins)
require("dotenv").config({ path: path.resolve(__dirname, "..", ".env") }); // repo-root master (fills gaps)

/**
 * Multi-chain Hardhat configuration for the L2 flash-loan arbitrage engine.
 *
 * RPC URLs and keys are read from environment variables (see .env.example).
 * Public fallback RPCs are provided so `npx hardhat compile` and the offline
 * unit tests work with zero configuration; set your own RPCs before deploying
 * or running mainnet-fork tests.
 */

const {
  PRIVATE_KEY,
  ETHERSCAN_API_KEY = "",
  OPTIMISM_RPC_URL = "https://mainnet.optimism.io",
  BASE_RPC_URL = "https://mainnet.base.org",
  ARBITRUM_RPC_URL = "https://arb1.arbitrum.io/rpc",
  INK_RPC_URL = "https://rpc-gel.inkonchain.com",
  UNICHAIN_RPC_URL = "https://mainnet.unichain.org",
  POLYGON_RPC_URL = "https://polygon-rpc.com",
  // Fork target for `npx hardhat test test/fork/*` — defaults to Arbitrum.
  FORK_RPC_URL = "",
  FORK_BLOCK = "",
} = process.env;

const accounts = PRIVATE_KEY ? [PRIVATE_KEY] : [];

/** @type import('hardhat/config').HardhatUserConfig */
module.exports = {
  solidity: {
    version: "0.8.20",
    settings: {
      optimizer: { enabled: true, runs: 1_000_000 },
      // Yul IR pipeline: better optimisation for the assembly/struct-heavy
      // hot path and avoids stack-too-deep without manual scratch structs.
      viaIR: true,
      // Compile target stays at solc 0.8.20's default (Paris) — the conservative
      // deploy bytecode. Cancun is an *execution*-spec concern (see the network
      // `hardfork` below), and solc 0.8.20 cannot target Cancun anyway (support
      // landed in 0.8.24). Foundry mirrors this: its `evm_version = "cancun"`
      // clamps the solc target to Shanghai while executing REVM at Cancun.
      metadata: { bytecodeHash: "none" },
    },
  },
  networks: {
    hardhat: {
      // Pin the local EVM to Cancun — the hardfork all five target L2s run
      // today (the bundled EDR otherwise defaults to a bleeding-edge fork that
      // mismatches the L2 fork history). Cancun is required to execute live
      // Aave V3.3 flash loans on a fork: their reentrancy guard uses EIP-1153
      // transient storage, which reverts with NotActivated under Shanghai.
      hardfork: "cancun",
      // When FORK_RPC_URL is set, the in-process node forks that chain so the
      // fork test-suite can exercise live pools. Otherwise it's a clean chain
      // for the offline unit tests.
      forking: FORK_RPC_URL
        ? { url: FORK_RPC_URL, blockNumber: FORK_BLOCK ? Number(FORK_BLOCK) : undefined }
        : undefined,
      // L2s aren't in Hardhat's built-in hardfork history, so tell it these
      // chains have been on a modern hardfork for the whole (recent) fork range.
      chains: {
        10: { hardforkHistory: { cancun: 0 } }, // Optimism
        137: { hardforkHistory: { cancun: 0 } }, // Polygon PoS
        130: { hardforkHistory: { cancun: 0 } }, // Unichain
        8453: { hardforkHistory: { cancun: 0 } }, // Base
        42161: { hardforkHistory: { cancun: 0 } }, // Arbitrum One
        57073: { hardforkHistory: { cancun: 0 } }, // Ink
      },
    },
    optimism: { url: OPTIMISM_RPC_URL, chainId: 10, accounts },
    base: { url: BASE_RPC_URL, chainId: 8453, accounts },
    arbitrum: { url: ARBITRUM_RPC_URL, chainId: 42161, accounts },
    ink: { url: INK_RPC_URL, chainId: 57073, accounts },
    unichain: { url: UNICHAIN_RPC_URL, chainId: 130, accounts },
    polygon: { url: POLYGON_RPC_URL, chainId: 137, accounts },
  },
  etherscan: {
    // Populate per-network keys as needed; L2 explorers use their own keys.
    apiKey: ETHERSCAN_API_KEY,
  },
  mocha: { timeout: 180_000 },
};
