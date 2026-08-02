import { randomUUID } from "node:crypto";
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, it, expect, beforeEach } from "vitest";
import request from "supertest";
import { ContractService, type ChainProbe } from "../src/contracts/service";
import type { ContractsPaths } from "../src/contracts/repo";
import { upsertEnv } from "../src/contracts/envFile";
import { buildServer, type AppHandles } from "../src/server";
import { loadEnv } from "../src/config/env";
import { makeOpportunity, StubProvider } from "./helpers";

/** Build an isolated on-disk contracts project fixture and its resolved paths. */
function makeFixture(opts: { compiled?: boolean; deployed?: boolean } = {}): {
  paths: ContractsPaths;
  root: string;
  deployedAddress: string;
} {
  const compiled = opts.compiled ?? true;
  const root = mkdtempSync(join(tmpdir(), "l2arb-contracts-"));
  const contractsDir = join(root, "contracts");
  const artifactsDir = join(contractsDir, "artifacts");
  const deploymentsDir = join(contractsDir, "deployments");
  mkdirSync(join(contractsDir, "config"), { recursive: true });

  // Address book: arbitrum verified, unichain unverified (null providers).
  writeFileSync(
    join(contractsDir, "config", "addresses.js"),
    `const ZERO = "0x0000000000000000000000000000000000000000";
const CHAINS = {
  arbitrum: { chainId: 42161, aavePool: "0x794a61358D6845594F94dc1DB02A252b5b4814aD", balancerVault: "0xBA12222222228d8Ba445958a75a0704d566BF2C8" },
  unichain: { chainId: 130, aavePool: null, balancerVault: null },
};
module.exports = { CHAINS, ZERO, forNetwork: (n) => CHAINS[n] };
`,
  );

  if (compiled) {
    const dir = join(artifactsDir, "contracts", "FlashLoanArbitrage.sol");
    mkdirSync(dir, { recursive: true });
    writeFileSync(
      join(dir, "FlashLoanArbitrage.json"),
      JSON.stringify({
        contractName: "FlashLoanArbitrage",
        abi: [{ type: "function", name: "aavePremiumBps", stateMutability: "view", inputs: [], outputs: [{ type: "uint256" }] }],
        bytecode: "0x6080604052",
      }),
    );
  }

  // Track a tracked .env.example template to seed .env from.
  writeFileSync(join(root, ".env.example"), "# master template\nPRIVATE_KEY=\nFLASH_LOAN_EXECUTOR_ADDRESS=\n");

  const paths: ContractsPaths = {
    repoRoot: root,
    contractsDir,
    artifactsDir,
    deploymentsDir,
    addressBook: join(contractsDir, "config", "addresses.js"),
    envFile: join(root, ".env"),
    envExample: join(root, ".env.example"),
    contractsPresent: true,
  };

  const deployedAddress = "0x1111111111111111111111111111111111111111";
  if (opts.deployed) {
    mkdirSync(deploymentsDir, { recursive: true });
    writeFileSync(
      join(deploymentsDir, "arbitrum.json"),
      JSON.stringify({ network: "arbitrum", chainId: 42161, address: deployedAddress, crossChainAddress: null, deployedAt: "2026-01-01T00:00:00.000Z" }),
    );
  }

  return { paths, root, deployedAddress };
}

const stubProbe: ChainProbe = {
  async getCodeSize(_chain, address) {
    return address === "0x1111111111111111111111111111111111111111" ? 14637 : 0;
  },
  async premiumBps() {
    return 5;
  },
};

describe("ContractService.status", () => {
  it("reports compile + per-chain action (verify-provider / deploy / ready)", () => {
    const { paths } = makeFixture({ compiled: true, deployed: true });
    const svc = new ContractService({ paths, chainProbe: stubProbe });
    const s = svc.status();

    expect(s.available).toBe(true);
    expect(s.compiled).toBe(true);
    const arb = s.networks.find((n) => n.key === "arbitrum")!;
    expect(arb.providerVerified).toBe(true);
    expect(arb.deployment?.address).toBe("0x1111111111111111111111111111111111111111");
    expect(arb.action).toBe("ready");

    const uni = s.networks.find((n) => n.key === "unichain")!;
    expect(uni.providerVerified).toBe(false);
    expect(uni.action).toBe("verify-provider");
  });

  it("says 'compile' when artifacts are missing, 'deploy' when compiled but undeployed", () => {
    const notCompiled = new ContractService({ paths: makeFixture({ compiled: false }).paths });
    expect(notCompiled.status().networks.find((n) => n.key === "arbitrum")!.action).toBe("compile");

    const compiledUndeployed = new ContractService({ paths: makeFixture({ compiled: true, deployed: false }).paths });
    expect(compiledUndeployed.status().networks.find((n) => n.key === "arbitrum")!.action).toBe("deploy");
  });
});

describe("ContractService.deployParams", () => {
  it("returns [aavePool, balancerVault, admin] for a verified chain", () => {
    const svc = new ContractService({ paths: makeFixture().paths });
    const admin = "0x2222222222222222222222222222222222222222";
    const p = svc.deployParams("arbitrum", admin);
    expect(p.args).toEqual([
      "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
      "0xBA12222222228d8Ba445958a75a0704d566BF2C8",
      admin,
    ]);
  });

  it("refuses an unverified-provider chain (never invents an address)", () => {
    const svc = new ContractService({ paths: makeFixture().paths });
    expect(() => svc.deployParams("unichain", "0x2222222222222222222222222222222222222222")).toThrow(/unverified/);
  });

  it("rejects a bad admin address", () => {
    const svc = new ContractService({ paths: makeFixture().paths });
    expect(() => svc.deployParams("arbitrum", "not-an-address")).toThrow(/valid 0x address/);
  });
});

describe("ContractService.getArtifact", () => {
  it("serves abi + bytecode when compiled", () => {
    const svc = new ContractService({ paths: makeFixture({ compiled: true }).paths });
    const a = svc.getArtifact("FlashLoanArbitrage");
    expect(a.bytecode.startsWith("0x")).toBe(true);
    expect(Array.isArray(a.abi)).toBe(true);
  });

  it("throws for an uncompiled contract and an unknown name", () => {
    const svc = new ContractService({ paths: makeFixture({ compiled: false }).paths });
    expect(() => svc.getArtifact("FlashLoanArbitrage")).toThrow(/not compiled/);
    expect(() => svc.getArtifact("Nope")).toThrow(/unknown contract/);
  });
});

describe("ContractService.recordDeployment", () => {
  it("writes the deployment record and upserts public addresses into .env", () => {
    const { paths } = makeFixture({ deployed: false });
    const svc = new ContractService({ paths });
    const addr = "0x3333333333333333333333333333333333333333";
    const { record, env } = svc.recordDeployment({
      network: "arbitrum",
      chainId: 42161,
      address: addr,
      crossChainAddress: "0x4444444444444444444444444444444444444444",
      deployedAt: "2026-02-02T00:00:00.000Z",
    });

    expect(record.address).toBe(addr);
    // deployments/arbitrum.json written
    const recFile = join(paths.deploymentsDir, "arbitrum.json");
    expect(existsSync(recFile)).toBe(true);
    expect(JSON.parse(readFileSync(recFile, "utf8")).address).toBe(addr);

    // .env seeded + patched with public address keys (never a secret).
    const envText = readFileSync(env.file, "utf8");
    expect(envText).toContain(`FLASH_LOAN_EXECUTOR_ADDRESS_ARBITRUM=${addr}`);
    expect(envText).toContain(`FLASH_LOAN_EXECUTOR_ADDRESS=${addr}`);
    expect(envText).toContain("CROSSCHAIN_EXECUTOR_ADDRESS_ARBITRUM=0x4444444444444444444444444444444444444444");
    expect(envText).toContain("EXECUTION_PROBE_CHAIN=arbitrum");
    // The seeded secret placeholder is preserved and left blank.
    expect(envText).toMatch(/^PRIVATE_KEY=\s*$/m);
  });

  it("does not clobber an operator's existing probe-chain / singular address", () => {
    const { paths } = makeFixture({ deployed: false });
    writeFileSync(paths.envFile, "FLASH_LOAN_EXECUTOR_ADDRESS=0x9999999999999999999999999999999999999999\nEXECUTION_PROBE_CHAIN=base\n");
    const svc = new ContractService({ paths });
    svc.recordDeployment({ network: "arbitrum", chainId: 42161, address: "0x3333333333333333333333333333333333333333", deployedAt: "2026-02-02T00:00:00.000Z" });
    const envText = readFileSync(paths.envFile, "utf8");
    expect(envText).toContain("FLASH_LOAN_EXECUTOR_ADDRESS=0x9999999999999999999999999999999999999999"); // unchanged
    expect(envText).toContain("EXECUTION_PROBE_CHAIN=base"); // unchanged
    expect(envText).toContain("FLASH_LOAN_EXECUTOR_ADDRESS_ARBITRUM=0x3333333333333333333333333333333333333333"); // new per-net
  });

  it("validates chainId/address and rejects mismatches", () => {
    const svc = new ContractService({ paths: makeFixture().paths });
    expect(() => svc.recordDeployment({ network: "arbitrum", chainId: 137, address: "0x3333333333333333333333333333333333333333" })).toThrow(/does not match/);
    expect(() => svc.recordDeployment({ network: "arbitrum", chainId: 42161, address: "0xdead" })).toThrow(/valid 0x address/);
    expect(() => svc.recordDeployment({ network: "nope", chainId: 1, address: "0x3333333333333333333333333333333333333333" })).toThrow(/unknown network/);
  });
});

describe("ContractService.runReadiness (read-only stress test)", () => {
  it("probes every deployed chain for bytecode + a view staticCall", async () => {
    const { paths } = makeFixture({ deployed: true });
    const svc = new ContractService({ paths, rpcUrls: { arbitrum: "https://rpc" }, chainProbe: stubProbe });
    const { results, probed } = await svc.runReadiness();
    expect(probed).toBe(true);
    const arb = results.find((r) => r.network === "arbitrum")!;
    expect(arb.hasCode).toBe(true);
    expect(arb.premiumBps).toBe(5);
    expect(arb.healthy).toBe(true);
  });

  it("reports no-code / unhealthy for a wrong address without throwing", async () => {
    const { paths } = makeFixture({ deployed: true });
    // Override the recorded address to one the stub reports as codeless.
    writeFileSync(join(paths.deploymentsDir, "arbitrum.json"), JSON.stringify({ network: "arbitrum", chainId: 42161, address: "0x5555555555555555555555555555555555555555", crossChainAddress: null, deployedAt: "x" }));
    const svc = new ContractService({ paths, rpcUrls: { arbitrum: "https://rpc" }, chainProbe: stubProbe });
    const arb = (await svc.runReadiness()).results.find((r) => r.network === "arbitrum")!;
    expect(arb.hasCode).toBe(false);
    expect(arb.healthy).toBe(false);
  });
});

describe("upsertEnv safety", () => {
  let file: string;
  beforeEach(() => {
    file = join(tmpdir(), `l2arb-env-${randomUUID()}`);
  });

  it("refuses to write a non-managed (potentially secret) key", () => {
    expect(() => upsertEnv({ file, values: { PRIVATE_KEY: "0xdeadbeef" } as never })).toThrow(/non-managed/);
  });

  it("creates the file and appends managed keys under a labelled block", () => {
    const r = upsertEnv({ file, values: { FLASH_LOAN_EXECUTOR_ADDRESS_BASE: "0xabc" } });
    expect(r.created).toBe(true);
    expect(readFileSync(file, "utf8")).toContain("FLASH_LOAN_EXECUTOR_ADDRESS_BASE=0xabc");
  });
});

describe("Contracts REST routes", () => {
  let handles: AppHandles;
  let deployedAddress: string;

  beforeEach(() => {
    const fx = makeFixture({ compiled: true, deployed: true });
    deployedAddress = fx.deployedAddress;
    handles = buildServer({
      env: { ...loadEnv(), dataSource: "simulated", executionMode: "paper" },
      provider: new StubProvider([makeOpportunity({ netProfitUsd: 10 })]),
      autoStartEngine: false,
      settingsFile: join(tmpdir(), `l2arb-test-settings-${randomUUID()}.json`),
      contractService: new ContractService({ paths: fx.paths, rpcUrls: { arbitrum: "https://rpc" }, chainProbe: stubProbe }),
    });
  });

  it("GET /api/contracts/status returns the monitor snapshot", async () => {
    const res = await request(handles.app).get("/api/contracts/status");
    expect(res.status).toBe(200);
    expect(res.body.compiled).toBe(true);
    expect(res.body.networks.find((n: { key: string }) => n.key === "arbitrum").action).toBe("ready");
  });

  it("GET /api/contracts/artifact/:name serves bytecode", async () => {
    const res = await request(handles.app).get("/api/contracts/artifact/FlashLoanArbitrage");
    expect(res.status).toBe(200);
    expect(res.body.bytecode.startsWith("0x")).toBe(true);
  });

  it("GET /api/contracts/deploy-params/:network resolves ctor args", async () => {
    const admin = "0x2222222222222222222222222222222222222222";
    const res = await request(handles.app).get(`/api/contracts/deploy-params/arbitrum?admin=${admin}`);
    expect(res.status).toBe(200);
    expect(res.body.args[2]).toBe(admin);
  });

  it("POST /api/contracts/deployment records a deployment", async () => {
    const res = await request(handles.app)
      .post("/api/contracts/deployment")
      .send({ network: "optimism", chainId: 10, address: "0x6666666666666666666666666666666666666666", deployedAt: "2026-03-03T00:00:00.000Z" });
    expect(res.status).toBe(200);
    expect(res.body.record.address).toBe("0x6666666666666666666666666666666666666666");
  });

  it("GET /api/contracts/readiness runs the read-only sweep", async () => {
    const res = await request(handles.app).get("/api/contracts/readiness");
    expect(res.status).toBe(200);
    expect(res.body.results.find((r: { network: string }) => r.network === "arbitrum").hasCode).toBe(true);
    expect(deployedAddress).toBe("0x1111111111111111111111111111111111111111");
  });
});
