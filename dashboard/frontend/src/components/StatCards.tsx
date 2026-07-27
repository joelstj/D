import type { ReactNode } from "react";
import { Activity, Crosshair, TrendingUp, Zap } from "lucide-react";
import { useLive } from "../hooks/useLiveData";
import { formatAmount, formatNumber, formatPct } from "../lib/format";
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
  const { stats, history, opportunities } = useLive();

  const pnl = stats?.realizedPnlUsd ?? 0;
  const daily = stats?.dailyPnlUsd ?? 0;
  const executed = stats?.executed ?? 0;
  const filled = stats?.filled ?? 0;
  const winRate = executed > 0 ? filled / executed : 0;
  const activeSeries = history.map((h) => h.active);
  // These aggregates sum per-opportunity `…Usd` magnitudes, so they are true USD
  // only when every active opportunity is USD-stablecoin-denominated. If any is
  // not, the totals are in numeraire units — don't fabricate a `$` on them.
  const allUsd = opportunities.every((o) => o.numeraireIsUsd !== false);
  const money = (v: number, sign = false) => formatAmount(v, { isUsd: allUsd, sign });

  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <Tile
        label={allUsd ? "Net P&L · paper" : "Net P&L · paper · numeraire units"}
        icon={<TrendingUp size={13} />}
        value={stats ? money(pnl, true) : "—"}
        valueClass={pnl > 0 ? "text-pos" : pnl < 0 ? "text-neg" : "text-ink"}
        sub={stats ? `Today ${money(daily, true)}` : undefined}
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
        value={stats ? money(stats.bestNetProfitUsd) : "—"}
        valueClass="text-ink"
        sub={stats ? `${formatNumber(stats.scans)} scans` : undefined}
      />
    </div>
  );
}
