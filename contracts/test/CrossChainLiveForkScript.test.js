const { expect } = require("chai");
const { ethers } = require("hardhat");
const { assertDualForkOnly, POLYGON, ARBITRUM } = require("../scripts/live_cross_chain_fork");

/**
 * Unit tests for scripts/live_cross_chain_fork.js.
 *
 * That script executes a REAL two-leg cross-chain arbitrage, so the guard
 * keeping it on the in-process fork is the single thing standing between it
 * and a live broadcast (root CLAUDE.md §2 invariant 3) — tested directly
 * rather than trusted, mirroring test/LiveForkScript.test.js's treatment of
 * the sibling same-chain script.
 *
 * Importing the script must not execute anything — it guards on
 * `require.main === module`.
 */
describe("live_cross_chain_fork script", () => {
  describe("assertDualForkOnly (broadcast guard)", () => {
    const bothSet = { POLYGON_RPC_URL: "https://example-polygon-rpc", ARBITRUM_RPC_URL: "https://example-arbitrum-rpc" };

    it("allows the in-process fork when both RPC URLs are set", () => {
      expect(() => assertDualForkOnly("hardhat", bothSet)).to.not.throw();
    });

    it("refuses every real network, so the script cannot be repointed at a live chain", () => {
      for (const net of ["optimism", "base", "arbitrum", "polygon", "ink", "unichain", "localhost"]) {
        expect(() => assertDualForkOnly(net, bothSet), net).to.throw(/Refusing to run on network/);
      }
    });

    it("refuses to run without POLYGON_RPC_URL", () => {
      expect(() => assertDualForkOnly("hardhat", { ARBITRUM_RPC_URL: bothSet.ARBITRUM_RPC_URL })).to.throw(
        /Set both POLYGON_RPC_URL and ARBITRUM_RPC_URL/
      );
    });

    it("refuses to run without ARBITRUM_RPC_URL", () => {
      expect(() => assertDualForkOnly("hardhat", { POLYGON_RPC_URL: bothSet.POLYGON_RPC_URL })).to.throw(
        /Set both POLYGON_RPC_URL and ARBITRUM_RPC_URL/
      );
    });

    it("refuses to run with neither RPC URL set", () => {
      expect(() => assertDualForkOnly("hardhat", {})).to.throw(/Set both POLYGON_RPC_URL and ARBITRUM_RPC_URL/);
    });

    it("checks the network guard before the RPC-URL guard", () => {
      // A real network with no RPC URLs configured must still fail on the
      // network check first — the more specific message would be misleading
      // (setting the URLs would not make it safe to run there).
      expect(() => assertDualForkOnly("polygon", {})).to.throw(/Refusing to run on network/);
    });
  });

  describe("chain address book", () => {
    it("only references addresses that are real, checksummed and distinct per chain", () => {
      for (const chain of [POLYGON, ARBITRUM]) {
        const fields = chain === POLYGON ? ["WMATIC", "WETH", "USDCe", "QUICKSWAP_ROUTER"] : ["WETH", "USDCe", "UNIV3_ROUTER02"];
        const seen = new Set();
        for (const field of fields) {
          expect(ethers.isAddress(chain[field]), `${chain.name}.${field}`).to.equal(true);
          expect(chain[field], `${chain.name}.${field} must not be the zero address`).to.not.equal(ethers.ZeroAddress);
          expect(seen.has(chain[field].toLowerCase()), `${chain.name}.${field} duplicates another address on the same chain`).to.equal(false);
          seen.add(chain[field].toLowerCase());
        }
      }
    });
  });
});
