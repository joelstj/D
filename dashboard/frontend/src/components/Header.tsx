import { Pause, Play, Zap } from "lucide-react";
import { useLive } from "../hooks/useLiveData";
import { WalletButton } from "./WalletButton";
import { Badge } from "./ui";

const STATUS_META = {
  open: { label: "Live", color: "var(--color-pos)" },
  connecting: { label: "Connecting", color: "var(--color-warn)" },
  closed: { label: "Offline", color: "var(--color-neg)" },
} as const;

export function Header() {
  const { status, stats, settings, patchSettings } = useLive();
  const s = STATUS_META[status];
  const running = stats?.running ?? false;
  const engineOn = settings?.engineEnabled ?? false;

  return (
    <header className="glass sticky top-0 z-30 border-b border-border">
      <div className="mx-auto flex max-w-[1600px] items-center gap-4 px-4 py-3 sm:px-6">
        <div className="flex items-center gap-2.5">
          <div className="grid h-9 w-9 place-items-center rounded-xl bg-accent shadow-lg shadow-accent/30">
            <Zap size={18} className="text-white" fill="white" />
          </div>
          <div className="leading-tight">
            <div className="text-sm font-semibold tracking-tight text-ink">L2 Arbitrage</div>
            <div className="text-[11px] text-ink-faint">Flash-Loan Engine</div>
          </div>
        </div>

        <div className="ml-1 flex items-center gap-1.5">
          <span
            className={`h-2 w-2 rounded-full ${status === "open" ? "live-dot" : ""}`}
            style={{ background: s.color }}
          />
          <span className="text-xs font-medium text-ink-muted">{s.label}</span>
        </div>

        <div className="ml-auto flex items-center gap-2 sm:gap-3">
          {stats && (
            <div className="hidden items-center gap-2 md:flex">
              <Badge tone={stats.dataSource === "live" ? "accent" : "neutral"}>
                {stats.dataSource === "live" ? "LIVE DATA" : "SIM DATA"}
              </Badge>
              <Badge tone={stats.executionMode === "live" ? "neg" : "neutral"}>
                {stats.executionMode === "live" ? "LIVE EXEC" : "PAPER"}
              </Badge>
            </div>
          )}

          <button
            type="button"
            onClick={() => patchSettings({ engineEnabled: !engineOn })}
            disabled={!settings}
            className={`focusable inline-flex items-center gap-1.5 rounded-xl border px-3 py-1.5 text-sm font-medium transition-colors disabled:opacity-40 ${
              running
                ? "border-pos/40 bg-pos-soft text-pos"
                : "border-border bg-surface-2 text-ink-muted"
            }`}
          >
            {running ? <Pause size={15} /> : <Play size={15} />}
            {running ? "Running" : "Paused"}
          </button>

          <WalletButton />
        </div>
      </div>
    </header>
  );
}
