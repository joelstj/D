import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  type ReactNode,
} from "react";
import { api, wsUrl } from "../lib/api";
import type {
  ArbitrageOpportunity,
  EngineStats,
  ExecutionResult,
  Settings,
  Snapshot,
  WsEnvelope,
} from "../lib/types";
import {
  initialLiveState,
  liveReducer,
  type Alert,
  type LiveState,
} from "./liveReducer";

export type { ConnectionStatus, Alert, HistoryPoint } from "./liveReducer";

interface LiveContextValue extends LiveState {
  patchSettings: (patch: Partial<Settings>) => Promise<void>;
  resetSettings: () => Promise<void>;
  execute: (id: string) => Promise<ExecutionResult>;
}

const LiveContext = createContext<LiveContextValue | null>(null);

export function LiveProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(liveReducer, initialLiveState);
  const wsRef = useRef<WebSocket | null>(null);
  const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const closedRef = useRef(false);

  useEffect(() => {
    closedRef.current = false;

    const connect = () => {
      dispatch({ type: "status", status: "connecting" });
      const ws = new WebSocket(wsUrl());
      wsRef.current = ws;

      ws.onopen = () => dispatch({ type: "status", status: "open" });
      ws.onmessage = (event) => {
        let msg: WsEnvelope;
        try {
          msg = JSON.parse(event.data);
        } catch {
          return;
        }
        switch (msg.type) {
          case "snapshot":
            dispatch({ type: "snapshot", snapshot: msg.payload as Snapshot });
            break;
          case "opportunity":
            dispatch({ type: "opportunity", opp: msg.payload as ArbitrageOpportunity });
            break;
          case "opportunity:remove":
            dispatch({ type: "opportunity:remove", id: (msg.payload as { id: string }).id });
            break;
          case "execution":
            dispatch({ type: "execution", result: msg.payload as ExecutionResult });
            break;
          case "stats":
            dispatch({ type: "stats", stats: msg.payload as EngineStats });
            break;
          case "settings":
            dispatch({ type: "settings", settings: msg.payload as Settings });
            break;
          case "alert": {
            const a = msg.payload as { level: Alert["level"]; message: string };
            dispatch({ type: "alert", alert: { ...a, ts: msg.ts } });
            break;
          }
        }
      };
      ws.onclose = () => {
        dispatch({ type: "status", status: "closed" });
        if (!closedRef.current) retryRef.current = setTimeout(connect, 1500);
      };
      ws.onerror = () => ws.close();
    };

    connect();

    return () => {
      closedRef.current = true;
      if (retryRef.current) clearTimeout(retryRef.current);
      wsRef.current?.close();
    };
  }, []);

  const patchSettings = useCallback(async (patch: Partial<Settings>) => {
    // Optimistic: apply locally immediately, then persist. The server echoes a
    // `settings` broadcast which reconciles all clients.
    dispatch({ type: "settings", settings: mergeSettings(patch) });
    try {
      const next = await api.patchSettings(patch);
      dispatch({ type: "settings", settings: next });
    } catch {
      // Reconcile from source of truth on failure.
      const current = await api.settings();
      dispatch({ type: "settings", settings: current });
    }
    function mergeSettings(p: Partial<Settings>): Settings {
      return { ...(state.settings as Settings), ...p };
    }
  }, [state.settings]);

  const resetSettings = useCallback(async () => {
    const next = await api.resetSettings();
    dispatch({ type: "settings", settings: next });
  }, []);

  const execute = useCallback((id: string) => api.execute(id), []);

  const value = useMemo<LiveContextValue>(
    () => ({ ...state, patchSettings, resetSettings, execute }),
    [state, patchSettings, resetSettings, execute],
  );

  return <LiveContext.Provider value={value}>{children}</LiveContext.Provider>;
}

export function useLive(): LiveContextValue {
  const ctx = useContext(LiveContext);
  if (!ctx) throw new Error("useLive must be used within <LiveProvider>");
  return ctx;
}
