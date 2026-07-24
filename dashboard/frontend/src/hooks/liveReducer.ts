import type {
  ArbitrageOpportunity,
  EngineStats,
  ExecutionResult,
  NetworkInfo,
  Settings,
  Snapshot,
} from "../lib/types";

export const MAX_OPPS = 60;
export const MAX_EXECS = 40;
export const MAX_ALERTS = 20;
export const MAX_HISTORY = 90;

export type ConnectionStatus = "connecting" | "open" | "closed";

export interface Alert {
  level: "info" | "warn" | "error";
  message: string;
  ts: number;
}

export interface HistoryPoint {
  ts: number;
  active: number;
  pnl: number;
}

export interface LiveState {
  status: ConnectionStatus;
  settings: Settings | null;
  networks: NetworkInfo[];
  opportunities: ArbitrageOpportunity[];
  executions: ExecutionResult[];
  stats: EngineStats | null;
  alerts: Alert[];
  history: HistoryPoint[];
}

export const initialLiveState: LiveState = {
  status: "connecting",
  settings: null,
  networks: [],
  opportunities: [],
  executions: [],
  stats: null,
  alerts: [],
  history: [],
};

export type LiveAction =
  | { type: "status"; status: ConnectionStatus }
  | { type: "snapshot"; snapshot: Snapshot }
  | { type: "opportunity"; opp: ArbitrageOpportunity }
  | { type: "opportunity:remove"; id: string }
  | { type: "execution"; result: ExecutionResult }
  | { type: "stats"; stats: EngineStats }
  | { type: "settings"; settings: Settings }
  | { type: "alert"; alert: Alert };

export function liveReducer(state: LiveState, action: LiveAction): LiveState {
  switch (action.type) {
    case "status":
      return { ...state, status: action.status };
    case "snapshot":
      return {
        ...state,
        settings: action.snapshot.settings,
        networks: action.snapshot.networks,
        opportunities: action.snapshot.opportunities.slice(0, MAX_OPPS),
        stats: action.snapshot.stats,
      };
    case "opportunity": {
      const deduped = state.opportunities.filter((o) => o.id !== action.opp.id);
      return { ...state, opportunities: [action.opp, ...deduped].slice(0, MAX_OPPS) };
    }
    case "opportunity:remove":
      return {
        ...state,
        opportunities: state.opportunities.filter((o) => o.id !== action.id),
      };
    case "execution":
      return {
        ...state,
        executions: [action.result, ...state.executions].slice(0, MAX_EXECS),
      };
    case "stats": {
      const point: HistoryPoint = {
        ts: action.stats.lastScanTs ?? Date.now(),
        active: action.stats.opportunitiesActive,
        pnl: action.stats.realizedPnlUsd,
      };
      return {
        ...state,
        stats: action.stats,
        history: [...state.history, point].slice(-MAX_HISTORY),
      };
    }
    case "settings":
      return { ...state, settings: action.settings };
    case "alert":
      return { ...state, alerts: [action.alert, ...state.alerts].slice(0, MAX_ALERTS) };
    default:
      return state;
  }
}
