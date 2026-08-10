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
  async paused() {
    return false;
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

  describe("contract parameter (D4)", () => {
    const admin = "0x2222222222222222222222222222222222222222";

    it("defaults to FlashLoanArbitrage when the contract param is omitted (backward compat)", () => {
      const svc = new ContractService({ paths: makeFixture().paths });
      const p = svc.deployParams("arbitrum", admin);
      expect(p.contract).toBe("FlashLoanArbitrage");
      expect(p.args).toHaveLength(3);
    });

    it("returns the 1-arg constructor(address admin) shape for CrossChainArbitrageExecutor", () => {
      const svc = new ContractService({ paths: makeFixture().paths });
      const p = svc.deployParams("arbitrum", admin, "CrossChainArbitrageExecutor");
      expect(p.contract).toBe("CrossChainArbitrageExecutor");
      expect(p.args).toEqual([admin]);
      expect(p.providerVerified).toBe(true);
    });

    it("does NOT require a verified flash-loan provider for the cross-chain contract", () => {
      // "unichain" has no verified Aave/Balancer addresses in the fixture — the
      // atomic contract refuses to deploy there (see the test above), but the
      // cross-chain executor has no provider dependency at all and must not be
      // blocked by that unrelated check.
      const svc = new ContractService({ paths: makeFixture().paths });
      const p = svc.deployParams("unichain", admin, "CrossChainArbitrageExecutor");
      expect(p.args).toEqual([admin]);
      expect(p.chainId).toBe(130);
    });

    it("still validates the admin address and network for the cross-chain contract", () => {
      const svc = new ContractService({ paths: makeFixture().paths });
      expect(() => svc.deployParams("arbitrum", "not-an-address", "CrossChainArbitrageExecutor")).toThrow(
        /valid 0x address/,
      );
      expect(() => svc.deployParams("nope", admin, "CrossChainArbitrageExecutor")).toThrow(/unknown network/);
    });

    it("rejects an unknown contract name", () => {
      const svc = new ContractService({ paths: makeFixture().paths });
      expect(() => svc.deployParams("arbitrum", admin, "NotAContract")).toThrow(/unknown contract/);
    });
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

  describe("paused()/crossChainPaused kill-switch reporting (O2)", () => {
    it("reports the live paused() state for the atomic executor", async () => {
      const { paths } = makeFixture({ deployed: true });
      const pausedProbe: ChainProbe = { ...stubProbe, async paused() { return true; } };
      const svc = new ContractService({ paths, rpcUrls: { arbitrum: "https://rpc" }, chainProbe: pausedProbe });
      const arb = (await svc.runReadiness()).results.find((r) => r.network === "arbitrum")!;
      expect(arb.hasCode).toBe(true);
      expect(arb.paused).toBe(true);
    });

    it("reports paused:false distinctly from paused:null (never guesses 'not paused')", async () => {
      const { paths } = makeFixture({ deployed: true });
      const svc = new ContractService({ paths, rpcUrls: { arbitrum: "https://rpc" }, chainProbe: stubProbe });
      const arb = (await svc.runReadiness()).results.find((r) => r.network === "arbitrum")!;
      expect(arb.paused).toBe(false);
    });

    it("reports paused:null (not false, not a thrown error) when the view reverts", async () => {
      const { paths } = makeFixture({ deployed: true });
      const revertingProbe: ChainProbe = {
        ...stubProbe,
        async paused() {
          throw new Error("execution reverted");
        },
      };
      const svc = new ContractService({ paths, rpcUrls: { arbitrum: "https://rpc" }, chainProbe: revertingProbe });
      const { results } = await svc.runReadiness();
      const arb = results.find((r) => r.network === "arbitrum")!;
      // The read-only sweep must not throw just because one view reverted —
      // it degrades that one field to "unknown", same discipline as premiumBps.
      expect(arb.paused).toBeNull();
      expect(arb.hasCode).toBe(true); // the rest of the probe still succeeded
    });

    it("reports crossChainPaused only when a cross-chain address is deployed and has code", async () => {
      const { paths } = makeFixture({ deployed: true });
      writeFileSync(
        join(paths.deploymentsDir, "arbitrum.json"),
        JSON.stringify({
          network: "arbitrum",
          chainId: 42161,
          address: "0x1111111111111111111111111111111111111111",
          crossChainAddress: "0x2222222222222222222222222222222222222299",
          deployedAt: "x",
        }),
      );
      const probe: ChainProbe = {
        async getCodeSize(_c, address) {
          return address.toLowerCase() === "0x2222222222222222222222222222222222222299".toLowerCase() ||
            address === "0x1111111111111111111111111111111111111111"
            ? 9000
            : 0;
        },
        async premiumBps() {
          return 5;
        },
        async paused(_c, address) {
          // Distinguish the two contracts' pause state so the test proves they
          // aren't accidentally sharing one result.
          return address === "0x1111111111111111111111111111111111111111";
        },
      };
      const svc = new ContractService({ paths, rpcUrls: { arbitrum: "https://rpc" }, chainProbe: probe });
      const arb = (await svc.runReadiness()).results.find((r) => r.network === "arbitrum")!;
      expect(arb.paused).toBe(true); // atomic contract, per the probe above
      expect(arb.crossChainHasCode).toBe(true);
      expect(arb.crossChainPaused).toBe(false); // cross-chain contract, per the probe above
    });

    it("leaves crossChainPaused null when no cross-chain address is on record at all", async () => {
      const { paths } = makeFixture({ deployed: true }); // fixture's arbitrum.json has crossChainAddress: null
      const svc = new ContractService({ paths, rpcUrls: { arbitrum: "https://rpc" }, chainProbe: stubProbe });
      const arb = (await svc.runReadiness()).results.find((r) => r.network === "arbitrum")!;
      expect(arb.crossChainAddress).toBeNull();
      expect(arb.crossChainPaused).toBeNull();
    });
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

  it("GET /api/contracts/deploy-params/:network?contract=CrossChainArbitrageExecutor resolves the 1-arg ctor (D4)", async () => {
    const admin = "0x2222222222222222222222222222222222222222";
    const res = await request(handles.app).get(
      `/api/contracts/deploy-params/arbitrum?admin=${admin}&contract=CrossChainArbitrageExecutor`,
    );
    expect(res.status).toBe(200);
    expect(res.body.contract).toBe("CrossChainArbitrageExecutor");
    expect(res.body.args).toEqual([admin]);
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
