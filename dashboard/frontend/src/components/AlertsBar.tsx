import { useEffect, useState } from "react";
import { AlertTriangle, X } from "lucide-react";
import { useLive } from "../hooks/useLiveData";

/** Transient toast for the most recent engine alert (risk limit hit, live-exec
 * refusal, scan error). Auto-dismisses; the full history lives in state. */
export function AlertsBar() {
  const { alerts } = useLive();
  const latest = alerts[0];
  const [dismissedTs, setDismissedTs] = useState(0);

  useEffect(() => {
    if (!latest) return;
    const id = setTimeout(() => setDismissedTs(latest.ts), 8000);
    return () => clearTimeout(id);
  }, [latest]);

  if (!latest || latest.ts === dismissedTs) return null;

  const tone =
    latest.level === "error"
      ? "border-neg/40 bg-neg-soft text-neg"
      : latest.level === "warn"
        ? "border-warn/40 bg-warn/5 text-warn"
        : "border-border bg-surface-2 text-ink-muted";

  return (
    <div className="fixed bottom-4 left-1/2 z-40 -translate-x-1/2 px-4">
      <div className={`flex items-center gap-2 rounded-xl border px-4 py-2.5 text-sm shadow-2xl ${tone}`}>
        <AlertTriangle size={15} />
        <span>{latest.message}</span>
        <button
          type="button"
          onClick={() => setDismissedTs(latest.ts)}
          className="focusable ml-2 rounded p-0.5 opacity-70 hover:opacity-100"
          aria-label="Dismiss"
        >
          <X size={14} />
        </button>
      </div>
    </div>
  );
}
