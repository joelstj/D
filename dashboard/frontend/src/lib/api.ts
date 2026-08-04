import type {
  ArbitrageOpportunity,
  CompileStatus,
  ContractArtifact,
  ContractsStatus,
  DeployParams,
  DeploymentRecord,
  EngineStats,
  ExecutionLatencySample,
  ExecutionResult,
  LatencySnapshot,
  NetworkInfo,
  ReadinessResult,
  Settings,
} from "./types";

/**
 * REST client for the backend. Base URL comes from VITE_API_URL; when empty the
 * app talks to the same origin and relies on the dev proxy (see vite.config.ts).
 * This same surface is what the language-agnostic SDKs use — the GUI has no
 * privileged backdoor.
 */
const BASE = (import.meta.env.VITE_API_URL ?? "").replace(/\/$/, "");

export function apiUrl(path: string): string {
  return `${BASE}${path}`;
}

/** Derive the websocket URL from the API base (or the current origin). */
export function wsUrl(): string {
  const origin = BASE || window.location.origin;
  return origin.replace(/^http/, "ws") + "/ws";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(apiUrl(path), {
    headers: { "content-type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let detail = "";
    try {
      detail = JSON.stringify(await res.json());
    } catch {
      /* ignore */
    }
    throw new Error(`${res.status} ${res.statusText} ${detail}`.trim());
  }
  return (await res.json()) as T;
}

export const api = {
  health: () => request<{ status: string; version: string }>("/api/health"),
  networks: () => request<{ networks: NetworkInfo[] }>("/api/networks"),
  settings: () => request<Settings>("/api/settings"),
  patchSettings: (patch: Partial<Settings>) =>
    request<Settings>("/api/settings", { method: "PATCH", body: JSON.stringify(patch) }),
  resetSettings: () => request<Settings>("/api/settings/reset", { method: "POST" }),
  opportunities: (limit = 100) =>
    request<{ opportunities: ArbitrageOpportunity[]; total: number }>(
      `/api/opportunities?limit=${limit}`,
    ),
  stats: () => request<EngineStats>("/api/stats"),
  latency: () => request<LatencySnapshot>("/api/latency"),
  executionLatency: () => request<ExecutionLatencySample>("/api/health/execution"),
  execute: (id: string) =>
    request<ExecutionResult>(`/api/execute/${id}`, { method: "POST" }),
  toggleEngine: (enabled: boolean) =>
    request<Settings>("/api/engine/toggle", {
      method: "POST",
      body: JSON.stringify({ enabled }),
    }),

  /**
   * Contracts surface (compile / deploy-record / status monitor / readiness).
   * The deploy transaction itself is signed by the operator's MetaMask in the
   * browser — these calls only compile, read status, resolve deploy arguments,
   * and record the *public result*. No key ever reaches the backend.
   */
  contracts: {
    status: () => request<ContractsStatus>("/api/contracts/status"),
    compile: () =>
      request<{ ok: boolean; output: string; artifacts: CompileStatus[] }>(
        "/api/contracts/compile",
        { method: "POST" },
      ),
    artifact: (name: string) => request<ContractArtifact>(`/api/contracts/artifact/${name}`),
    /** `contract` defaults (server-side) to the atomic `FlashLoanArbitrage`;
     *  pass `"CrossChainArbitrageExecutor"` for its 1-arg constructor. */
    deployParams: (network: string, admin: string, contract?: string) =>
      request<DeployParams>(
        `/api/contracts/deploy-params/${network}?admin=${encodeURIComponent(admin)}` +
          (contract ? `&contract=${encodeURIComponent(contract)}` : ""),
      ),
    recordDeployment: (body: {
      network: string;
      chainId: number;
      address: string;
      crossChainAddress?: string | null;
      deployer?: string;
      txHash?: string;
      deployedAt?: string;
    }) =>
      request<{ record: DeploymentRecord; env: { file: string; created: boolean; updatedKeys: string[] } }>(
        "/api/contracts/deployment",
        { method: "POST", body: JSON.stringify(body) },
      ),
    readiness: () =>
      request<{ results: ReadinessResult[]; probed: boolean }>("/api/contracts/readiness"),
  },
};
