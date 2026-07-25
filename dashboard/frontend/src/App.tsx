import { Header } from "./components/Header";
import { StatCards } from "./components/StatCards";
import { OpportunitiesTable } from "./components/OpportunitiesTable";
import { SettingsPanel } from "./components/SettingsPanel";
import { LatencyPanel } from "./components/LatencyPanel";
import { ExecutionsLog } from "./components/ExecutionsLog";
import { AlertsBar } from "./components/AlertsBar";

export default function App() {
  return (
    <div className="min-h-full">
      <Header />
      <main className="mx-auto max-w-[1600px] px-4 py-5 sm:px-6">
        <StatCards />

        <div className="mt-4 grid gap-4 xl:grid-cols-3">
          <div className="space-y-4 xl:col-span-2">
            <OpportunitiesTable />
            <ExecutionsLog />
          </div>
          <div className="space-y-4 xl:col-span-1">
            <div className="xl:sticky xl:top-20 space-y-4">
              <SettingsPanel />
              <LatencyPanel />
            </div>
          </div>
        </div>

        <footer className="mt-6 rounded-xl border border-border-soft bg-surface/40 px-4 py-3 text-[11px] leading-relaxed text-ink-faint">
          <strong className="text-ink-muted">Paper / simulation mode.</strong> Opportunities and
          fills shown here are modelled for research and UI development — no transactions are
          broadcast. Live execution requires a deployed, audited flash-loan contract, a funded
          signer, and MEV protection, and involves real financial risk. Nothing here is financial
          advice.
        </footer>
      </main>
      <AlertsBar />
    </div>
  );
}
