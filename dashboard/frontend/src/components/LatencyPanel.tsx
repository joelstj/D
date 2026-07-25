import { useEffect, useState } from "react";
import { Radio } from "lucide-react";
import { useLive } from "../hooks/useLiveData";
import { api } from "../lib/api";
import type {
  ComponentLatency,
  ExecutionLatencySample,
  LatencySnapshot,
  StageStat,
} from "../lib/types";
import { Card, Badge } from "./ui";

/** Human labels for the pipeline components, in flow order. */
const COMPONENT_LABEL: Record<string, string> = {
  ingestion: "Ingestion · Rust",
  engine: "Detection · Python",
  dashboard: "Dashboard · Node",
  pipeline: "Cross-process",
};

/** Compact, adaptive-precision millisecond formatter. */
export function fmtMs(ms: number): string {
  if (!Number.isFinite(ms)) return "—";
  if (ms >= 1000) return `${(ms / 1000).toFixed(2)}s`;
  if (ms >= 100) return `${ms.toFixed(0)}ms`;
  if (ms >= 10) return `${ms.toFixed(1)}ms`;
  return `${ms.toFixed(2)}ms`;
}

function mean(xs: number[]): number {
  return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0;
}

function StageRow({ stat, max }: { stat: StageStat; max: number }) {
  const pct = max > 0 ? Math.min(100, (stat.p50 / max) * 100) : 0;
  return (
    <div className="flex items-center gap-2 py-1">
      <div className="w-32 shrink-0 truncate text-xs text-ink-muted" title={stat.stage}>
        {stat.stage}
      </div>
      <div className="relative h-2 flex-1 overflow-hidden rounded-full bg-surface-3">
        <div className="absolute inset-y-0 left-0 rounded-full bg-accent" style={{ width: `${pct}%` }} />
      </div>
      <div className="tabular w-16 shrink-0 text-right text-xs text-ink">{fmtMs(stat.p50)}</div>
      <div className="tabular w-16 shrink-0 text-right text-[11px] text-ink-faint" title="p95">
        {fmtMs(stat.p95)}
      </div>
    </div>
  );
}

function ComponentBlock({ comp }: { comp: ComponentLatency }) {
  const max = Math.max(...comp.stages.map((s) => s.p50), 0.0001);
  return (
    <div className="py-1.5">
      <div className="mb-0.5 text-[11px] font-medium uppercase tracking-wide text-ink-faint">
        {COMPONENT_LABEL[comp.component] ?? comp.component}
      </div>
      {comp.stages.map((s) => (
        <StageRow key={s.stage} stat={s} max={max} />
      ))}
    </div>
  );
}

function Headline({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div className="rounded-lg border border-border-soft bg-surface-2 px-3 py-2">
      <div className="text-[11px] uppercase tracking-wide text-ink-faint">{label}</div>
      <div className="tabular mt-0.5 text-xl font-semibold text-ink">{value}</div>
      <div className="text-[11px] text-ink-faint">{sub}</div>
    </div>
  );
}

/**
 * Pure presentational latency HUD (exported for unit tests). Shows the end-to-end
 * pipeline latency headline, a per-component stage breakdown (p50 bar, p95 label),
 * the client-measured "ingest → your browser" number, and — separately — the
 * read-only on-chain execution-readiness probe.
 */
export function LatencyPanelView({
  snapshot,
  clientLatencyMs,
  execution,
}: {
  snapshot: LatencySnapshot | null;
  clientLatencyMs: number[];
  execution: ExecutionLatencySample | null;
}) {
  const e2e = snapshot?.endToEnd ?? null;
  const clientLast = clientLatencyMs.length ? clientLatencyMs[clientLatencyMs.length - 1]! : null;
  const components = snapshot?.components ?? [];
  // Keep a stable, readable order; "pipeline" (cross-process gap) renders last.
  const ordered = [
    ...components.filter((c) => c.component !== "pipeline"),
    ...components.filter((c) => c.component === "pipeline"),
  ];

  return (
    <Card
      title="Pipeline Latency"
      subtitle="Ingest → detect → display, per stage"
      right={
        <Badge tone={snapshot?.anchored ? "accent" : "neutral"}>
          {snapshot?.anchored ? "live feed" : "no feed"}
        </Badge>
      }
    >
      <div className="mb-3 grid grid-cols-2 gap-3">
        <Headline
          label="Ingest → dashboard"
          value={e2e ? fmtMs(e2e.p50) : "—"}
          sub={e2e ? `p95 ${fmtMs(e2e.p95)} · ${e2e.count} samples` : "awaiting ingestion feed"}
        />
        <Headline
          label="Ingest → your browser"
          value={clientLast !== null ? fmtMs(clientLast) : "—"}
          sub={
            clientLatencyMs.length
              ? `avg ${fmtMs(mean(clientLatencyMs))} · ${clientLatencyMs.length} samples`
              : "—"
          }
        />
      </div>

      {ordered.length === 0 ? (
        <div className="py-3 text-xs text-ink-faint">
          No stage samples yet. In paper/simulated mode only the dashboard's own stages are
          measurable — connect the ingestion feed (DATA_SOURCE=external) for the full pipeline.
        </div>
      ) : (
        <>
          <div className="divide-y divide-border-soft">
            {ordered.map((c) => (
              <ComponentBlock key={c.component} comp={c} />
            ))}
          </div>
          <div className="mt-1 flex items-center justify-between text-[10px] text-ink-faint">
            <span>bar = p50 · right = p95</span>
            <span>single-host wall clock</span>
          </div>
        </>
      )}

      <ExecutionReadiness execution={execution} />
    </Card>
  );
}

function ExecutionReadiness({ execution }: { execution: ExecutionLatencySample | null }) {
  const tone = !execution || !execution.configured ? "neutral" : execution.healthy ? "pos" : "neg";
  const label = !execution
    ? "…"
    : !execution.configured
      ? "not configured"
      : execution.healthy
        ? "healthy"
        : "error";
  return (
    <div className="mt-3 border-t border-border-soft pt-3">
      <div className="mb-1 flex items-center justify-between">
        <div className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-ink-faint">
          <Radio size={12} /> On-chain execution readiness
        </div>
        <Badge tone={tone}>{label}</Badge>
      </div>
      {!execution || !execution.configured ? (
        <div className="text-[11px] text-ink-faint">
          Read-only RPC probe (block · gas · optional staticCall). Set RPC_URL_[NETWORK] to enable.
          It never broadcasts — execution stays paper by default.
        </div>
      ) : execution.healthy ? (
        <div className="space-y-1">
          {execution.stages.map((s) => (
            <div key={s.stage} className="flex items-center justify-between text-xs">
              <span className="text-ink-muted">{s.stage}</span>
              <span className="tabular text-ink">{fmtMs(s.ms)}</span>
            </div>
          ))}
          <div className="flex items-center justify-between pt-0.5 text-[11px] text-ink-faint">
            <span>
              {execution.chain} · block {execution.blockNumber}
            </span>
            <span>{execution.gasPriceGwei != null ? `${execution.gasPriceGwei} gwei` : ""}</span>
          </div>
        </div>
      ) : (
        <div className="text-[11px] text-neg">{execution.error ?? "probe failed"}</div>
      )}
    </div>
  );
}

/** Container: reads live pipeline latency from context and polls the (separate)
 *  read-only execution-readiness probe on an interval. */
export function LatencyPanel() {
  const { latency, clientLatencyMs } = useLive();
  const [execution, setExecution] = useState<ExecutionLatencySample | null>(null);

  useEffect(() => {
    let alive = true;
    const poll = () =>
      api
        .executionLatency()
        .then((s) => {
          if (alive) setExecution(s);
        })
        .catch(() => {
          /* health probe is best-effort; ignore transient errors */
        });
    poll();
    const id = setInterval(poll, 5000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  return (
    <LatencyPanelView snapshot={latency} clientLatencyMs={clientLatencyMs} execution={execution} />
  );
}
