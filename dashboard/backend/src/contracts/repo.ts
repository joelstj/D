import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

/**
 * Locate the merged **D** super-repo root from this module's location, the same
 * anchor `config/env.ts` uses: walk up until we find a directory that holds the
 * sibling component folders (`engine/` + `dashboard/`). Anchoring on the module
 * (not `process.cwd()`) keeps it correct whether the backend runs from `tsx`
 * source or the bundled `dist/index.js`, regardless of the launcher's CWD.
 *
 * Returns `null` when no such root is found (e.g. the dashboard was vendored out
 * on its own) so callers can degrade gracefully instead of throwing.
 */
export function findRepoRoot(startDir?: string): string | null {
  let dir = startDir ?? dirname(fileURLToPath(import.meta.url));
  for (let i = 0; i < 12; i++) {
    if (existsSync(resolve(dir, "engine")) && existsSync(resolve(dir, "dashboard"))) {
      return dir;
    }
    const parent = dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  return null;
}

/** Resolved filesystem paths the contracts tooling reads/writes. */
export interface ContractsPaths {
  /** Super-repo root. */
  repoRoot: string;
  /** The `contracts/` Hardhat project. */
  contractsDir: string;
  /** Compiled Hardhat artifacts root (`contracts/artifacts`). */
  artifactsDir: string;
  /** Deploy-record directory (`contracts/deployments`, git-ignored). */
  deploymentsDir: string;
  /** The static per-chain provider address book (`contracts/config/addresses.js`). */
  addressBook: string;
  /** The master `.env` at the repo root (git-ignored). */
  envFile: string;
  /** The tracked `.env.example` template. */
  envExample: string;
  /** True when the `contracts/` project is actually present next to us. */
  contractsPresent: boolean;
}

/**
 * Resolve every path the contracts service needs. When the repo root can't be
 * found, paths are still returned (best-effort, relative to a fallback) with
 * `contractsPresent:false` so the API can report "contracts project unavailable"
 * rather than crash.
 */
export function resolveContractsPaths(root?: string | null): ContractsPaths {
  const repoRoot = root ?? findRepoRoot() ?? process.cwd();
  const contractsDir = resolve(repoRoot, "contracts");
  return {
    repoRoot,
    contractsDir,
    artifactsDir: resolve(contractsDir, "artifacts"),
    deploymentsDir: resolve(contractsDir, "deployments"),
    addressBook: resolve(contractsDir, "config", "addresses.js"),
    envFile: resolve(repoRoot, ".env"),
    envExample: resolve(repoRoot, ".env.example"),
    contractsPresent: existsSync(contractsDir),
  };
}
