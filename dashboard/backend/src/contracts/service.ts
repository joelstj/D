import { createHash } from "node:crypto";
import { execFile } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { resolve } from "node:path";
import { NETWORKS, NETWORKS_BY_KEY } from "../arbitrage/networks";
import { createLogger } from "../util/logger";
import { resolveContractsPaths, type ContractsPaths } from "./repo";
import { envSuffix, upsertEnv } from "./envFile";

const log = createLogger("contracts");
const require = createRequire(import.meta.url);

/** Contracts the dashboard can compile/deploy, and where their artifacts live. */
export const CONTRACTS = [
  {
    name: "FlashLoanArbitrage",
    role: "atomic" as const,
    artifact: "contracts/FlashLoanArbitrage.sol/FlashLoanArbitrage.json",
    /** constructor(address aavePool, address balancerVault, address admin) */
    constructorInputs: ["aavePool", "balancerVault", "admin"],
  },
  {
    name: "CrossChainArbitrageExecutor",
    role: "crosschain" as const,
    artifact: "contracts/crosschain/CrossChainArbitrageExecutor.sol/CrossChainArbitrageExecutor.json",
    /** constructor(address admin) */
    constructorInputs: ["admin"],
  },
];

const ZERO = "0x0000000000000000000000000000000000000000";

export interface CompileStatus {
  name: string;
  role: "atomic" | "crosschain";
  compiled: boolean;
  bytecodeHash: string | null;
  bytecodeSize: number | null;
}

export interface DeploymentRecord {
  network: string;
  chainId: number;
  address: string;
  crossChainAddress: string | null;
  aavePool?: string;
  balancerVault?: string;
  deployer?: string;
  txHash?: string;
  deployedAt: string;
}

/** What the operator should do next for a given chain — the monitor's headline. */
export type ContractAction = "verify-provider" | "compile" | "deploy" | "ready";

export interface NetworkContractStatus {
  key: string;
  name: string;
  chainId: number;
  explorer: string;
  /** Both flash-loan providers known (else the address book needs verification). */
  providerVerified: boolean;
  aavePool: string | null;
  balancerVault: string | null;
  deployment: DeploymentRecord | null;
  /** A per-network executor address is present in the master `.env`. */
  envWired: boolean;
  action: ContractAction;
}

export interface ContractsStatus {
  available: boolean;
  contractsDir: string;
  compiled: boolean;
  artifacts: CompileStatus[];
  networks: NetworkContractStatus[];
  generatedAt: number;
}

/** Result of one chain's read-only readiness probe (the deployment stress test). */
export interface ReadinessResult {
  network: string;
  chainId: number;
  address: string | null;
  crossChainAddress: string | null;
  /** RPC configured for this chain (else we can't probe it). */
  configured: boolean;
  /** Bytecode is present at the FlashLoanArbitrage address (it's really deployed). */
  hasCode: boolean;
  /** `aavePremiumBps()` staticCall succeeded — the ABI + address line up. */
  premiumBps: number | null;
  /** Cross-chain executor bytecode present (when a cross-chain address is on record). */
  crossChainHasCode: boolean | null;
  healthy: boolean;
  error: string | null;
}

/** Injected shell runner (tests bypass the real Hardhat compile). */
export type CompileRunner = (contractsDir: string) => Promise<{ ok: boolean; output: string }>;

/** Read-only, per-chain contract probe (tests inject a stub; prod uses viem). */
export interface ChainProbe {
  /** Deployed-bytecode length in bytes at `address` on `chainKey` (0 ⇒ no contract). */
  getCodeSize(chainKey: string, address: string): Promise<number>;
  /** `staticCall` of `aavePremiumBps()`; rejects if the address isn't an executor. */
  premiumBps(chainKey: string, address: string): Promise<number>;
}

export interface ContractServiceOptions {
  paths?: ContractsPaths;
  /** RPC endpoints per network key (for the readiness sweep). */
  rpcUrls?: Record<string, string | undefined>;
  /** Deployed executor addresses per network key already wired via env. */
  envExecutors?: Record<string, string | undefined>;
  compileRunner?: CompileRunner;
  chainProbe?: ChainProbe;
}

/** Load the static per-chain provider address book (`config/addresses.js`). */
function loadAddressBook(paths: ContractsPaths): Record<string, { chainId: number; aavePool: string | null; balancerVault: string | null }> {
  try {
    if (!existsSync(paths.addressBook)) return {};
    // Fresh require is fine — the address book is static config, not artifacts.
    const mod = require(paths.addressBook) as {
      CHAINS: Record<string, { chainId: number; aavePool: string | null; balancerVault: string | null }>;
    };
    return mod.CHAINS ?? {};
  } catch (err) {
    log.warn(`could not load address book: ${String(err)}`);
    return {};
  }
}

const ADDR_RE = /^0x[0-9a-fA-F]{40}$/;
export function isAddress(x: unknown): x is string {
  return typeof x === "string" && ADDR_RE.test(x);
}

/**
 * Read-only + config-writing service backing the dashboard Contracts panel.
 *
 * Everything here is safe under invariant 3: it compiles (no chain), reads chain
 * state, and records addresses the operator's own MetaMask already deployed. It
 * holds no key, builds no signer, and never broadcasts — deployment is signed in
 * the browser wallet and only its *result* (a public address) is recorded here.
 */
export class ContractService {
  readonly paths: ContractsPaths;
  private readonly rpcUrls: Record<string, string | undefined>;
  private readonly envExecutors: Record<string, string | undefined>;
  private readonly compileRunner: CompileRunner;
  private readonly chainProbe?: ChainProbe;

  constructor(opts: ContractServiceOptions = {}) {
    this.paths = opts.paths ?? resolveContractsPaths();
    this.rpcUrls = opts.rpcUrls ?? {};
    this.envExecutors = opts.envExecutors ?? {};
    this.compileRunner = opts.compileRunner ?? defaultCompileRunner;
    this.chainProbe = opts.chainProbe;
  }

  /** Read a compiled artifact fresh from disk (never cached — recompiles matter). */
  private readArtifact(rel: string): { abi: unknown[]; bytecode: string; contractName: string } | null {
    const file = resolve(this.paths.artifactsDir, rel);
    if (!existsSync(file)) return null;
    try {
      const json = JSON.parse(readFileSync(file, "utf8"));
      if (typeof json.bytecode !== "string" || !Array.isArray(json.abi)) return null;
      return { abi: json.abi, bytecode: json.bytecode, contractName: json.contractName };
    } catch (err) {
      log.warn(`bad artifact ${rel}: ${String(err)}`);
      return null;
    }
  }

  private compileStatus(): CompileStatus[] {
    return CONTRACTS.map((c) => {
      const art = this.readArtifact(c.artifact);
      const hasCode = !!art && art.bytecode.length > 2;
      return {
        name: c.name,
        role: c.role,
        compiled: hasCode,
        bytecodeHash: hasCode ? bytecodeHash(art!.bytecode) : null,
        bytecodeSize: hasCode ? (art!.bytecode.length - 2) / 2 : null,
      };
    });
  }

  /** Load a deploy record written by `scripts/deploy.js` or by this service. */
  deploymentRecord(networkKey: string): DeploymentRecord | null {
    const file = resolve(this.paths.deploymentsDir, `${networkKey}.json`);
    if (!existsSync(file)) return null;
    try {
      return JSON.parse(readFileSync(file, "utf8")) as DeploymentRecord;
    } catch {
      return null;
    }
  }

  /** The full status snapshot the Contracts monitor renders. */
  status(): ContractsStatus {
    const book = loadAddressBook(this.paths);
    const artifacts = this.compileStatus();
    const atomicCompiled = artifacts.find((a) => a.role === "atomic")?.compiled ?? false;

    const networks: NetworkContractStatus[] = NETWORKS.map((n) => {
      const b = book[n.key];
      const aavePool = b?.aavePool ?? null;
      const balancerVault = b?.balancerVault ?? null;
      const providerVerified = !!aavePool || !!balancerVault;
      const deployment = this.deploymentRecord(n.key);
      const envWired = !!this.envExecutors[n.key];

      let action: ContractAction;
      if (!providerVerified) action = "verify-provider";
      else if (!atomicCompiled) action = "compile";
      else if (!deployment) action = "deploy";
      else action = "ready";

      return {
        key: n.key,
        name: n.name,
        chainId: n.chainId,
        explorer: n.explorer,
        providerVerified,
        aavePool,
        balancerVault,
        deployment,
        envWired,
        action,
      };
    });

    return {
      available: this.paths.contractsPresent,
      contractsDir: this.paths.contractsDir,
      compiled: atomicCompiled,
      artifacts,
      networks,
      generatedAt: Date.now(),
    };
  }

  /** Serve a compiled artifact's ABI + bytecode for a browser-wallet deploy. */
  getArtifact(name: string): { contractName: string; abi: unknown[]; bytecode: string } {
    const entry = CONTRACTS.find((c) => c.name === name);
    if (!entry) throw new Error(`unknown contract "${name}"`);
    const art = this.readArtifact(entry.artifact);
    if (!art || art.bytecode.length <= 2) {
      throw new Error(`"${name}" is not compiled — run compile first`);
    }
    return { contractName: entry.name, abi: art.abi, bytecode: art.bytecode };
  }

  /**
   * Constructor arguments for a browser-wallet deploy of the atomic executor on
   * `networkKey`. The caller supplies `admin` (their connected wallet); provider
   * addresses come from the verified address book. Never invents an address:
   * an unverified chain returns `providerVerified:false` so the UI blocks deploy.
   */
  deployParams(networkKey: string, admin: string): {
    network: string;
    chainId: number;
    contract: string;
    providerVerified: boolean;
    aavePool: string;
    balancerVault: string;
    args: string[];
  } {
    const net = NETWORKS_BY_KEY[networkKey];
    if (!net) throw new Error(`unknown network "${networkKey}"`);
    if (!isAddress(admin)) throw new Error("admin must be a valid 0x address (connect your wallet)");
    const book = loadAddressBook(this.paths)[networkKey];
    const aavePool = book?.aavePool ?? null;
    const balancerVault = book?.balancerVault ?? null;
    const providerVerified = !!aavePool || !!balancerVault;
    if (!providerVerified) {
      throw new Error(
        `flash-loan provider addresses for "${networkKey}" are unverified (config/addresses.js) — ` +
          `verify Aave/Balancer on this chain before deploying`,
      );
    }
    return {
      network: networkKey,
      chainId: net.chainId,
      contract: "FlashLoanArbitrage",
      providerVerified,
      aavePool: aavePool ?? ZERO,
      balancerVault: balancerVault ?? ZERO,
      args: [aavePool ?? ZERO, balancerVault ?? ZERO, admin],
    };
  }

  /**
   * Persist a deployment the operator's wallet already broadcast: write the
   * `deployments/<network>.json` record (same shape `scripts/deploy.js` writes)
   * and upsert the public address into the master `.env`. Validates everything;
   * never accepts or writes a secret.
   */
  recordDeployment(input: {
    network: string;
    chainId: number;
    address: string;
    crossChainAddress?: string | null;
    deployer?: string;
    txHash?: string;
    deployedAt?: string;
  }): { record: DeploymentRecord; env: { file: string; created: boolean; updatedKeys: string[] } } {
    const net = NETWORKS_BY_KEY[input.network];
    if (!net) throw new Error(`unknown network "${input.network}"`);
    if (net.chainId !== input.chainId) {
      throw new Error(`chainId ${input.chainId} does not match network "${input.network}" (${net.chainId})`);
    }
    if (!isAddress(input.address)) throw new Error("address must be a valid 0x address");
    if (input.crossChainAddress != null && input.crossChainAddress !== "" && !isAddress(input.crossChainAddress)) {
      throw new Error("crossChainAddress must be a valid 0x address");
    }
    if (input.deployer != null && input.deployer !== "" && !isAddress(input.deployer)) {
      throw new Error("deployer must be a valid 0x address");
    }
    if (input.txHash != null && input.txHash !== "" && !/^0x[0-9a-fA-F]{64}$/.test(input.txHash)) {
      throw new Error("txHash must be a 0x-prefixed 32-byte hash");
    }

    const record: DeploymentRecord = {
      network: input.network,
      chainId: input.chainId,
      address: input.address,
      crossChainAddress: input.crossChainAddress || null,
      deployer: input.deployer || undefined,
      txHash: input.txHash || undefined,
      // Caller-supplied ISO string (the frontend stamps it) or a fixed marker —
      // the backend avoids Date in tests, but real writes get a real timestamp.
      deployedAt: input.deployedAt || new Date().toISOString(),
    };

    if (!this.paths.contractsPresent) {
      throw new Error("contracts project not found next to the dashboard — cannot record a deployment");
    }
    mkdirSync(this.paths.deploymentsDir, { recursive: true });
    const recordFile = resolve(this.paths.deploymentsDir, `${input.network}.json`);
    writeFileSync(recordFile, JSON.stringify(record, null, 2) + "\n", "utf8");

    // Upsert the public addresses into the master .env (never a secret).
    const suffix = envSuffix(input.network);
    const values: Record<string, string> = {
      [`FLASH_LOAN_EXECUTOR_ADDRESS_${suffix}`]: input.address,
      // Fill the singular probe var + probe chain only if the operator left them
      // empty, so we never override an explicit choice.
      FLASH_LOAN_EXECUTOR_ADDRESS: input.address,
      EXECUTION_PROBE_CHAIN: input.network,
    };
    if (record.crossChainAddress) {
      values[`CROSSCHAIN_EXECUTOR_ADDRESS_${suffix}`] = record.crossChainAddress;
    }
    const env = upsertEnv({
      file: this.paths.envFile,
      seedFrom: this.paths.envExample,
      values,
      onlyIfEmpty: ["FLASH_LOAN_EXECUTOR_ADDRESS", "EXECUTION_PROBE_CHAIN"],
    });

    log.info(`recorded ${input.network} deployment ${input.address} (env: ${env.updatedKeys.join(", ")})`);
    return { record, env: { file: this.paths.envFile, created: env.created, updatedKeys: env.updatedKeys } };
  }

  /** Compile the contracts (server-side Hardhat; no chain, no key). */
  async compile(): Promise<{ ok: boolean; output: string; artifacts: CompileStatus[] }> {
    if (!this.paths.contractsPresent) {
      return { ok: false, output: "contracts project not found next to the dashboard", artifacts: [] };
    }
    const { ok, output } = await this.compileRunner(this.paths.contractsDir);
    return { ok, output, artifacts: this.compileStatus() };
  }

  /**
   * Read-only deployment stress test: for every chain with a recorded deployment,
   * confirm bytecode is actually present and `aavePremiumBps()` staticCalls
   * cleanly. Pure observability — no signer, no broadcast (invariant 3).
   */
  async runReadiness(): Promise<{ results: ReadinessResult[]; probed: boolean }> {
    const probe = this.chainProbe;
    const results: ReadinessResult[] = [];
    for (const n of NETWORKS) {
      const dep = this.deploymentRecord(n.key);
      if (!dep) continue;
      const configured = !!this.rpcUrls[n.key] || !!probe;
      const base: ReadinessResult = {
        network: n.key,
        chainId: n.chainId,
        address: dep.address,
        crossChainAddress: dep.crossChainAddress,
        configured,
        hasCode: false,
        premiumBps: null,
        crossChainHasCode: dep.crossChainAddress ? false : null,
        healthy: false,
        error: null,
      };
      if (!probe || !configured) {
        results.push({ ...base, error: configured ? "no chain probe available" : "no RPC configured for this chain" });
        continue;
      }
      try {
        const size = await probe.getCodeSize(n.key, dep.address);
        const hasCode = size > 0;
        let premiumBps: number | null = null;
        if (hasCode) {
          try {
            premiumBps = await probe.premiumBps(n.key, dep.address);
          } catch (err) {
            // A view revert means the address/ABI don't line up — record, don't throw.
            premiumBps = null;
            base.error = `aavePremiumBps() reverted: ${String(err)}`;
          }
        }
        let crossChainHasCode: boolean | null = null;
        if (dep.crossChainAddress) {
          crossChainHasCode = (await probe.getCodeSize(n.key, dep.crossChainAddress)) > 0;
        }
        results.push({
          ...base,
          hasCode,
          premiumBps,
          crossChainHasCode,
          healthy: hasCode && (premiumBps !== null),
          error: base.error,
        });
      } catch (err) {
        results.push({ ...base, error: String(err) });
      }
    }
    return { results, probed: !!probe && results.some((r) => r.configured) };
  }
}

function bytecodeHash(bytecode: string): string {
  return "sha256:" + createHash("sha256").update(bytecode).digest("hex").slice(0, 16);
}

/** Real Hardhat compile via `npx hardhat compile` in the contracts project. */
const defaultCompileRunner: CompileRunner = (contractsDir) =>
  new Promise((resolvePromise) => {
    execFile(
      "npx",
      ["hardhat", "compile"],
      { cwd: contractsDir, timeout: 180_000, maxBuffer: 8 * 1024 * 1024 },
      (err, stdout, stderr) => {
        const output = `${stdout || ""}${stderr || ""}`.trim();
        resolvePromise({ ok: !err, output: output || (err ? String(err) : "compiled") });
      },
    );
  });
