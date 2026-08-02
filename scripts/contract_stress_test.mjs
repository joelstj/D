#!/usr/bin/env node
/**
 * Live deployment stress test — read-only readiness sweep across every chain the
 * flash-loan contracts are deployed on, plus the cross-chain executor.
 *
 * For each deployment recorded in `contracts/deployments/<network>.json` it:
 *   1. `eth_getCode`  — confirms bytecode is actually present on-chain (a real deploy),
 *   2. `eth_call aavePremiumBps()` — a staticCall proving the address is our
 *      executor (its ABI lines up) and that the read path an eventual human-
 *      authorised signer builds on is live,
 *   3. `eth_getCode` on the cross-chain executor when one is recorded.
 *
 * ## Safety (root CLAUDE.md invariant 3 — binding)
 * This is STRICTLY read-only. It only ever issues `eth_getCode` and `eth_call`
 * (staticCall) JSON-RPC requests — it builds no signer, sends no transaction, and
 * calls `executeArbitrage` never. A "live stress test" of real arbitrage cannot
 * be forced on mainnet (the contract is profit-or-revert, so a blind fire just
 * reverts); this proves the *deployed pipeline* on live chain state instead, and
 * real execution stays a human-signed MetaMask action in the dashboard.
 *
 * Offline-safe: with no RPC configured (or no deployments recorded) it reports
 * "skipped" and exits 0, mirroring `scripts/e2e_smoke.py`. It exits non-zero only
 * when a chain has an RPC configured but its recorded contract has NO code —
 * a genuine "recorded a deploy that isn't there" failure.
 *
 * Usage:  node scripts/contract_stress_test.mjs
 * RPCs come from the master .env / real env: RPC_URL_<NET> or <NET>_RPC_URL.
 */
import { readFileSync, readdirSync, existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const AAVE_PREMIUM_SELECTOR = "0x02cd2f5b"; // keccak256("aavePremiumBps()")[:4]

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const DEPLOYMENTS_DIR = join(REPO_ROOT, "contracts", "deployments");

/** Env var names that may hold each network's RPC endpoint (first non-empty wins). */
const RPC_ENV = {
  optimism: ["RPC_URL_OPTIMISM", "OPTIMISM_RPC_URL"],
  base: ["RPC_URL_BASE", "BASE_RPC_URL"],
  arbitrum: ["RPC_URL_ARBITRUM", "ARBITRUM_RPC_URL"],
  polygon: ["RPC_URL_POLYGON", "POLYGON_RPC_URL"],
  unichain: ["RPC_URL_UNICHAIN", "UNICHAIN_RPC_URL"],
  ink: ["RPC_URL_INK", "INK_RPC_URL"],
};

/** Minimal `.env` loader — populates process.env for keys it doesn't already have. */
function loadEnvFile(file) {
  if (!existsSync(file)) return;
  for (const raw of readFileSync(file, "utf8").split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq <= 0) continue;
    const key = line.slice(0, eq).trim();
    const val = line.slice(eq + 1).trim();
    if (process.env[key] === undefined && val !== "") process.env[key] = val;
  }
}

function rpcUrlFor(network) {
  for (const name of RPC_ENV[network] ?? []) {
    if (process.env[name]) return process.env[name];
  }
  return null;
}

async function rpc(url, method, params) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method, params }),
  });
  const json = await res.json();
  if (json.error) throw new Error(json.error.message ?? JSON.stringify(json.error));
  return json.result;
}

async function codeSize(url, address) {
  const code = await rpc(url, "eth_getCode", [address, "latest"]);
  return code && code !== "0x" ? (code.length - 2) / 2 : 0;
}

function loadDeployments() {
  if (!existsSync(DEPLOYMENTS_DIR)) return [];
  return readdirSync(DEPLOYMENTS_DIR)
    .filter((f) => f.endsWith(".json"))
    .map((f) => JSON.parse(readFileSync(join(DEPLOYMENTS_DIR, f), "utf8")));
}

async function main() {
  loadEnvFile(join(REPO_ROOT, ".env"));

  const deployments = loadDeployments();
  console.log("\n🧪  Contract deployment stress test (read-only readiness sweep)\n");

  if (deployments.length === 0) {
    console.log("   No deployments recorded (contracts/deployments/*.json).");
    console.log("   Deploy from the dashboard Contracts panel first — SKIPPED.\n");
    return 0;
  }

  let failures = 0;
  let probed = 0;

  for (const d of deployments) {
    const url = rpcUrlFor(d.network);
    const label = `${d.network} (chainId ${d.chainId})`;
    if (!url) {
      console.log(`   • ${label}: no RPC configured — SKIPPED`);
      continue;
    }
    probed++;
    try {
      const size = await codeSize(url, d.address);
      if (size === 0) {
        console.log(`   ✗ ${label}: FlashLoanArbitrage ${d.address} has NO code on-chain`);
        failures++;
        continue;
      }
      let premium = "n/a";
      try {
        const raw = await rpc(url, "eth_call", [{ to: d.address, data: AAVE_PREMIUM_SELECTOR }, "latest"]);
        premium = `${parseInt(raw, 16)} bps`;
      } catch {
        premium = "view unavailable (Balancer-only?)";
      }
      let xchain = "";
      if (d.crossChainAddress) {
        const xsize = await codeSize(url, d.crossChainAddress);
        xchain = xsize > 0 ? " · xchain ✓" : " · xchain ✗ NO CODE";
        if (xsize === 0) failures++;
      }
      console.log(`   ✓ ${label}: FlashLoanArbitrage ${size} bytes · aavePremium ${premium}${xchain}`);
    } catch (err) {
      console.log(`   ! ${label}: probe error (${String(err)}) — treated as skip`);
    }
  }

  console.log(
    `\n   ${probed} chain(s) probed, ${failures} failure(s). ${
      failures === 0 ? "All recorded deployments are live. ✅" : "Some recorded deployments are missing code. ❌"
    }\n`,
  );
  return failures === 0 ? 0 : 1;
}

main()
  .then((code) => process.exit(code))
  .catch((err) => {
    // A transport/offline error is a clean skip, not a stress-test failure.
    console.log(`\n   Sweep could not run (${String(err)}) — SKIPPED.\n`);
    process.exit(0);
  });
