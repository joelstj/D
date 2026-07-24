import type {
  ArbitrageOpportunity,
  EngineStats,
  ExecutionResult,
  NetworkInfo,
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
  execute: (id: string) =>
    request<ExecutionResult>(`/api/execute/${id}`, { method: "POST" }),
  toggleEngine: (enabled: boolean) =>
    request<Settings>("/api/engine/toggle", {
      method: "POST",
      body: JSON.stringify({ enabled }),
    }),
};
