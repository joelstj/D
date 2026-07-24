import type { ReactNode } from "react";
import { Activity, Crosshair, TrendingUp, Zap } from "lucide-react";
import { useLive } from "../hooks/useLiveData";
import { formatUsd, formatNumber, formatPct } from "../lib/format";
import { Sparkline } from "./Sparkline";

function Tile({
  label,
  value,
  sub,
  icon,
  valueClass = "text-ink",
  right,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  icon: ReactNode;
  valueClass?: string;
  right?: ReactNode;
}) {
  return (
    <div className="card flex items-center justify-between gap-3 px-4 py-3.5">
      <div className="min-w-0">
        <div className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-ink-faint">
          <span className="text-ink-faint">{icon}</span>
          {label}
        </div>
        <div className={`tabular mt-1 text-2xl font-semibold tracking-tight ${valueClass}`}>
          {value}
        </div>
        {sub && <div className="tabular mt-0.5 text-xs text-ink-faint">{sub}</div>}
      </div>
      {right}
    </div>
  );
}

export function StatCards() {
  const { stats, history } = useLive();

  const pnl = stats?.realizedPnlUsd ?? 0;
  const daily = stats?.dailyPnlUsd ?? 0;
  const executed = stats?.executed ?? 0;
  const filled = stats?.filled ?? 0;
  const winRate = executed > 0 ? filled / executed : 0;
  const activeSeries = history.map((h) => h.active);

  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <Tile
        label="Net P&L · paper"
        icon={<TrendingUp size={13} />}
        value={stats ? formatUsd(pnl, { sign: true }) : "—"}
        valueClass={pnl > 0 ? "text-pos" : pnl < 0 ? "text-neg" : "text-ink"}
        sub={stats ? `Today ${formatUsd(daily, { sign: true })}` : undefined}
      />
      <Tile
        label="Active Opps"
        icon={<Crosshair size={13} />}
        value={stats ? formatNumber(stats.opportunitiesActive) : "—"}
        sub={stats ? `${formatNumber(stats.opportunitiesDetected)} detected` : undefined}
        right={<Sparkline values={activeSeries} width={112} height={40} />}
      />
      <Tile
        label="Executions"
        icon={<Zap size={13} />}
        value={stats ? formatNumber(executed) : "—"}
        sub={
          stats
            ? `${formatNumber(filled)} filled · ${executed ? formatPct(winRate) : "—"} win`
            : undefined
        }
      />
      <Tile
        label="Best Net / Trade"
        icon={<Activity size={13} />}
        value={stats ? formatUsd(stats.bestNetProfitUsd) : "—"}
        valueClass="text-ink"
        sub={stats ? `${formatNumber(stats.scans)} scans` : undefined}
      />
    </div>
  );
}
