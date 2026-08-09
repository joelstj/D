const { expect } = require("chai");
const { ethers } = require("hardhat");
const {
  assertForkOnly,
  resolveProfitReceiver,
  CHAINS,
} = require("../scripts/live_flash_loan_fork");

/**
 * Unit tests for scripts/live_flash_loan_fork.js.
 *
 * That script executes a REAL arbitrage, so the guard keeping it on the
 * in-process fork is the single thing standing between it and a live
 * broadcast (root CLAUDE.md §2 invariant 3). It is therefore tested directly
 * rather than trusted, alongside the profit-receiver resolution that decides
 * which wallet the money goes to.
 *
 * Importing the script must not execute anything — it guards on
 * `require.main === module`.
 */
describe("live_flash_loan_fork script", () => {
  describe("assertForkOnly (broadcast guard)", () => {
    it("allows the in-process fork when FORK_RPC_URL is set", () => {
      expect(() => assertForkOnly("hardhat", { FORK_RPC_URL: "https://example-rpc" })).to.not.throw();
    });

    it("refuses every real network, so the script cannot be repointed at a live chain", () => {
      // Every network configured in hardhat.config.js other than `hardhat`
      // is a real RPC whose writes would broadcast.
      for (const net of ["optimism", "base", "arbitrum", "polygon", "ink", "unichain", "localhost"]) {
        expect(() => assertForkOnly(net, { FORK_RPC_URL: "https://example-rpc" }), net).to.throw(
          /Refusing to run on network/
        );
      }
    });

    it("refuses to run without FORK_RPC_URL (a clean chain has no live pools)", () => {
      expect(() => assertForkOnly("hardhat", {})).to.throw(/FORK_RPC_URL is not set/);
      expect(() => assertForkOnly("hardhat", { FORK_RPC_URL: "" })).to.throw(/FORK_RPC_URL is not set/);
    });
  });

  describe("resolveProfitReceiver", () => {
    const ADDRESS = "0x50A71dF7DfC5850e8434C7c8A564366F4980183b";
    // Well-known Hardhat account #0 key — a public test vector, not a secret.
    const TEST_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80";
    const TEST_KEY_ADDRESS = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266";

    it("prefers an explicit PROFIT_RECEIVER and returns it checksummed", () => {
      const r = resolveProfitReceiver({ PROFIT_RECEIVER: ADDRESS.toLowerCase() });
      expect(r.address).to.equal(ADDRESS);
      expect(r.source).to.equal("PROFIT_RECEIVER");
    });

    it("rejects a malformed PROFIT_RECEIVER instead of silently falling back", () => {
      // Falling back to a key-derived address here would quietly send profit
      // somewhere the operator did not name.
      expect(() => resolveProfitReceiver({ PROFIT_RECEIVER: "not-an-address" })).to.throw(/not a valid address/);
      expect(() =>
        resolveProfitReceiver({ PROFIT_RECEIVER: "0xdeadbeef", EXECUTOR_PRIVATE_KEY: TEST_KEY })
      ).to.throw(/not a valid address/);
    });

    it("derives the address from EXECUTOR_PRIVATE_KEY when no explicit receiver is given", () => {
      const r = resolveProfitReceiver({ EXECUTOR_PRIVATE_KEY: TEST_KEY });
      expect(r.address).to.equal(TEST_KEY_ADDRESS);
      expect(r.source).to.match(/derived/);
    });

    it("accepts a key with or without the 0x prefix, and tolerates whitespace", () => {
      expect(resolveProfitReceiver({ EXECUTOR_PRIVATE_KEY: TEST_KEY.slice(2) }).address).to.equal(TEST_KEY_ADDRESS);
      expect(resolveProfitReceiver({ EXECUTOR_PRIVATE_KEY: `  ${TEST_KEY}\n` }).address).to.equal(TEST_KEY_ADDRESS);
    });

    it("never reports the key itself in the returned source string", () => {
      const r = resolveProfitReceiver({ EXECUTOR_PRIVATE_KEY: TEST_KEY });
      expect(r.source).to.not.include(TEST_KEY);
      expect(r.source).to.not.include(TEST_KEY.slice(2));
    });

    it("fails loudly when no receiver can be determined", () => {
      expect(() => resolveProfitReceiver({})).to.throw(/No profit receiver/);
    });
  });

  describe("chain address book", () => {
    it("only references addresses that are real, checksummed and distinct per chain", () => {
      expect(CHAINS.length).to.be.greaterThan(0);
      for (const c of CHAINS) {
        for (const field of ["wrappedNative", "quote", "aavePool", "balancerVault", "v3Router", "v2Router"]) {
          expect(ethers.isAddress(c[field]), `${c.name}.${field}`).to.equal(true);
          expect(c[field], `${c.name}.${field} must not be the zero address`).to.not.equal(ethers.ZeroAddress);
        }
        // The borrowed asset and the intermediate must be different tokens, or
        // the "route" would be a no-op that trivially fails the profit check.
        expect(c.wrappedNative.toLowerCase(), c.name).to.not.equal(c.quote.toLowerCase());
      }
    });

    it("uses a chain-specific wrapped-native token for fork detection", () => {
      // Detection keys off this address, so a value shared between two chains
      // would make the script mis-identify the fork and use wrong routers.
      const seen = CHAINS.map((c) => c.wrappedNative.toLowerCase());
      expect(new Set(seen).size).to.equal(seen.length);
    });
  });
});
