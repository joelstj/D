import { useState } from "react";
import { ArrowRight, Loader2, Zap } from "lucide-react";
import { useLive } from "../hooks/useLiveData";
import { useNow } from "../hooks/useNow";
import { formatAmount, formatBps, formatPct, timeAgo } from "../lib/format";
import { networkColor, dexLabel } from "../lib/networkMeta";
import type { ArbitrageOpportunity } from "../lib/types";
import { Card } from "./ui";

const MAX_ROWS = 18;

export function OpportunitiesTable() {
  const { opportunities, settings, execute } = useLive();
  const now = useNow(1000);
  const [executing, setExecuting] = useState<Set<string>>(new Set());

  const rows = [...opportunities]
    .sort((a, b) => b.netProfitUsd - a.netProfitUsd)
    .slice(0, MAX_ROWS);

  const onExecute = async (id: string) => {
    setExecuting((s) => new Set(s).add(id));
    try {
      await execute(id);
    } catch {
      /* surfaced via alerts */
    } finally {
      setExecuting((s) => {
        const n = new Set(s);
        n.delete(id);
        return n;
      });
    }
  };

  return (
    <Card
      title="Live Arbitrage Opportunities"
      subtitle="Ranked by net profit after flash-loan, DEX, and gas costs"
      right={
        <span className="tabular rounded-lg border border-border bg-surface-2 px-2.5 py-1 text-xs text-ink-muted">
          {opportunities.length} active
        </span>
      }
      bodyClassName="px-0 pb-2"
    >
      <div className="scroll-thin max-h-[560px] overflow-auto">
        <table className="w-full border-collapse text-sm">
          <thead className="sticky top-0 z-10 bg-surface/95 backdrop-blur">
            <tr className="text-left text-[11px] uppercase tracking-wide text-ink-faint">
              <th className="px-5 py-2 font-medium">Network</th>
              <th className="px-3 py-2 font-medium">Route</th>
              <th className="px-3 py-2 text-right font-medium">Spread</th>
              <th className="px-3 py-2 text-right font-medium">Loan</th>
              <th className="px-3 py-2 text-right font-medium">Net Profit</th>
              <th className="px-3 py-2 font-medium">Confidence</th>
              <th className="px-3 py-2 text-right font-medium">Age</th>
              <th className="px-5 py-2 text-right font-medium"></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((o) => (
              <Row
                key={o.id}
                o={o}
                now={now}
                executing={executing.has(o.id)}
                paper={settings?.executionMode !== "live"}
                onExecute={() => onExecute(o.id)}
              />
            ))}
          </tbody>
        </table>

        {rows.length === 0 && (
          <div className="flex flex-col items-center justify-center gap-2 py-16 text-center">
            <Loader2 size={22} className="spin text-ink-faint" />
            <p className="text-sm text-ink-muted">Scanning venues for profitable dislocations…</p>
            <p className="text-xs text-ink-faint">
              Opportunities appear here the moment net profit clears your thresholds.
            </p>
          </div>
        )}
      </div>
    </Card>
  );
}

function Row({
  o,
  now,
  executing,
  paper,
  onExecute,
}: {
  o: ArbitrageOpportunity;
  now: number;
  executing: boolean;
  paper: boolean;
  onExecute: () => void;
}) {
  const tokens = [o.route[0]?.tokenIn, ...o.route.map((l) => l.tokenOut)].filter(Boolean);
  const ttl = Math.max(0, Math.round((o.expiresAt - now) / 1000));
  // Only show a `$` when the numeraire is a USD stablecoin; otherwise the figures
  // are numeraire base units and are labeled with the token symbol instead.
  const isUsd = o.numeraireIsUsd !== false;

  return (
    <tr className="row-in border-t border-border-soft hover:bg-surface-2/60">
      <td className="px-5 py-2.5">
        <div className="flex items-center gap-2">
          <span className="h-2 w-2 rounded-full" style={{ background: networkColor(o.network) }} />
          <span className="text-ink capitalize">{o.network}</span>
        </div>
      </td>
      <td className="px-3 py-2.5">
        <div className="flex items-center gap-1.5 text-ink">
          {tokens.map((t, i) => (
            <span key={i} className="flex items-center gap-1.5">
              {i > 0 && <ArrowRight size={11} className="text-ink-faint" />}
              <span className="text-xs font-medium">{t}</span>
            </span>
          ))}
        </div>
        <div className="mt-0.5 text-[11px] text-ink-faint">
          {o.route.map((l) => dexLabel(l.dex)).join(" → ")}
        </div>
      </td>
      <td className="tabular px-3 py-2.5 text-right text-ink-muted">{formatBps(o.spreadBps)}</td>
      <td className="tabular px-3 py-2.5 text-right text-ink-muted">
        {formatAmount(o.amountInUsd, { isUsd, symbol: o.tokenIn, compact: true })}
      </td>
      <td className="px-3 py-2.5 text-right">
        <div className="tabular font-semibold text-pos">
          {formatAmount(o.netProfitUsd, { isUsd, symbol: o.tokenIn, sign: true })}
        </div>
        <div className="tabular text-[11px] text-ink-faint">{o.profitBps.toFixed(1)} bps</div>
      </td>
      <td className="px-3 py-2.5">
        <div className="flex items-center gap-2">
          <div className="h-1.5 w-16 overflow-hidden rounded-full bg-surface-3">
            <div
              className="h-full rounded-full"
              style={{
                width: `${Math.round(o.confidence * 100)}%`,
                background:
                  o.confidence > 0.66
                    ? "var(--color-pos)"
                    : o.confidence > 0.4
                      ? "var(--color-warn)"
                      : "var(--color-neg)",
              }}
            />
          </div>
          <span className="tabular text-xs text-ink-faint">{formatPct(o.confidence)}</span>
        </div>
      </td>
      <td className="tabular px-3 py-2.5 text-right text-ink-faint">
        <span title={`expires in ~${ttl}s`}>{timeAgo(o.ts, now)}</span>
      </td>
      <td className="px-5 py-2.5 text-right">
        <button
          type="button"
          onClick={onExecute}
          disabled={executing}
          title={paper ? "Simulated fill (paper mode)" : "Broadcast live transaction"}
          className="focusable inline-flex items-center gap-1.5 rounded-lg border border-accent/40 bg-accent-soft px-2.5 py-1 text-xs font-medium text-accent-2 transition-opacity hover:opacity-90 disabled:opacity-50"
        >
          {executing ? <Loader2 size={12} className="spin" /> : <Zap size={12} />}
          {executing ? "…" : "Execute"}
        </button>
      </td>
    </tr>
  );
}
