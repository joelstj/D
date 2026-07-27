import { CheckCircle2, Inbox, XCircle } from "lucide-react";
import { useLive } from "../hooks/useLiveData";
import { useNow } from "../hooks/useNow";
import { formatAmount, timeAgo } from "../lib/format";
import { networkColor } from "../lib/networkMeta";
import { Card } from "./ui";

export function ExecutionsLog() {
  const { executions } = useLive();
  const now = useNow(1000);

  return (
    <Card
      title="Execution Activity"
      subtitle="Recent fills and reverts"
      bodyClassName="px-0 pb-2"
    >
      {executions.length === 0 ? (
        <div className="flex flex-col items-center gap-2 py-10 text-center">
          <Inbox size={20} className="text-ink-faint" />
          <p className="text-sm text-ink-muted">No executions yet</p>
          <p className="text-xs text-ink-faint">
            Enable auto-execute or hit “Execute” on an opportunity.
          </p>
        </div>
      ) : (
        <ul className="scroll-thin max-h-[260px] divide-y divide-border-soft overflow-auto">
          {executions.map((e) => {
            const filled = e.status === "filled";
            return (
              <li key={e.id} className="flex items-center gap-3 px-5 py-2.5">
                {filled ? (
                  <CheckCircle2 size={16} className="shrink-0 text-pos" />
                ) : (
                  <XCircle size={16} className="shrink-0 text-neg" />
                )}
                <span
                  className="h-2 w-2 shrink-0 rounded-full"
                  style={{ background: networkColor(e.network) }}
                />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm capitalize text-ink">
                      {e.network} <span className="text-ink-faint">· {e.status}</span>
                    </span>
                    <span
                      className={`tabular text-sm font-semibold ${
                        e.realizedProfitUsd >= 0 ? "text-pos" : "text-neg"
                      }`}
                    >
                      {formatAmount(e.realizedProfitUsd, {
                        isUsd: e.numeraireIsUsd !== false,
                        sign: true,
                      })}
                    </span>
                  </div>
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate text-[11px] text-ink-faint">{e.notes}</span>
                    <span className="tabular shrink-0 text-[11px] text-ink-faint">
                      {timeAgo(e.ts, now)}
                    </span>
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}
