import type { ReactNode } from "react";
import { AlertTriangle, RotateCcw } from "lucide-react";
import { useLive } from "../hooks/useLiveData";
import type { Settings } from "../lib/types";
import { networkColor, dexLabel } from "../lib/networkMeta";
import { formatUsd } from "../lib/format";
import {
  Card,
  ChipToggle,
  NumberField,
  ParamSlider,
  Segmented,
  ToggleRow,
} from "./ui";

const TOKEN_UNIVERSE = ["USDC", "USDT", "DAI", "WETH", "WBTC", "ARB", "OP", "MATIC", "AERO"];

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="border-t border-border-soft px-5 py-4 first:border-t-0">
      <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-ink-faint">
        {title}
      </h3>
      {children}
    </div>
  );
}

export function SettingsPanel() {
  const { settings, networks, patchSettings, resetSettings } = useLive();

  if (!settings) {
    return (
      <Card title="Parameters">
        <div className="py-8 text-center text-sm text-ink-faint">Loading configuration…</div>
      </Card>
    );
  }

  const set = <K extends keyof Settings>(key: K, value: Settings[K]) =>
    patchSettings({ [key]: value } as Partial<Settings>);

  // DEX options available across the currently-enabled networks.
  const availableDexes = Array.from(
    new Set(
      networks
        .filter((n) => settings.networks.includes(n.key))
        .flatMap((n) => n.dexes.map((d) => d.key)),
    ),
  );

  const toggleIn = (list: string[], value: string, min: number): string[] => {
    const has = list.includes(value);
    if (has && list.length <= min) return list; // enforce schema minimums
    return has ? list.filter((v) => v !== value) : [...list, value];
  };

  return (
    <Card
      title="Strategy Parameters"
      subtitle="Every control is wired to the engine — changes take effect on the next scan"
      right={
        <button
          type="button"
          onClick={resetSettings}
          className="focusable inline-flex items-center gap-1 rounded-lg border border-border bg-surface-2 px-2 py-1 text-xs text-ink-muted hover:text-ink"
        >
          <RotateCcw size={12} /> Reset
        </button>
      }
      bodyClassName="px-0 pb-2"
    >
      <div>
        <Section title="Execution">
          <div className="flex items-center justify-between py-2">
            <span className="text-sm text-ink-muted">Execution mode</span>
            <Segmented
              value={settings.executionMode}
              onChange={(v) => set("executionMode", v)}
              options={[
                { value: "paper", label: "Paper" },
                { value: "live", label: "Live" },
              ]}
            />
          </div>
          {settings.executionMode === "live" && (
            <div className="mb-2 flex items-start gap-2 rounded-lg border border-warn/30 bg-warn/5 px-3 py-2 text-xs text-warn">
              <AlertTriangle size={14} className="mt-0.5 shrink-0" />
              <span>
                Live execution requires a deployed, audited flash-loan contract and a funded
                signer. Until configured, the engine refuses live fills.
              </span>
            </div>
          )}
          <ToggleRow
            label="Auto-execute"
            hint="Fire qualifying opportunities automatically"
            checked={settings.autoExecute}
            onChange={(v) => set("autoExecute", v)}
          />
          <ParamSlider
            label="Scan interval"
            value={settings.scanIntervalMs}
            min={250}
            max={10000}
            step={250}
            unit="ms"
            onChange={(v) => set("scanIntervalMs", v)}
          />
        </Section>

        <Section title="Strategy">
          <NumberField
            label="Flash-loan size"
            value={settings.loanAmountUsd}
            min={100}
            step={1000}
            unit="USD"
            onCommit={(v) => set("loanAmountUsd", v)}
          />
          <div className="flex items-center justify-between py-2">
            <span className="text-sm text-ink-muted">Flash-loan provider</span>
            <Segmented
              value={settings.flashLoanProvider}
              onChange={(v) => set("flashLoanProvider", v)}
              options={[
                { value: "aave-v3", label: "Aave" },
                { value: "balancer-v2", label: "Balancer" },
                { value: "uniswap-v3", label: "Uni v3" },
              ]}
            />
          </div>
          <div className="py-2">
            <div className="mb-2 text-sm text-ink-muted">Base asset</div>
            <div className="flex flex-wrap gap-1.5">
              {settings.tokens.map((t) => (
                <ChipToggle
                  key={t}
                  label={t}
                  active={settings.baseToken === t}
                  onClick={() => set("baseToken", t)}
                />
              ))}
            </div>
          </div>
          <NumberField
            label="Min net profit"
            value={settings.minProfitUsd}
            min={0}
            step={5}
            unit="USD"
            onCommit={(v) => set("minProfitUsd", v)}
          />
          <ParamSlider
            label="Min profit margin"
            value={settings.minProfitBps}
            min={0}
            max={100}
            step={1}
            unit="bps"
            onChange={(v) => set("minProfitBps", v)}
          />
          <ParamSlider
            label="Max slippage"
            value={settings.slippageBps}
            min={0}
            max={200}
            step={1}
            unit="bps"
            onChange={(v) => set("slippageBps", v)}
          />
          <ParamSlider
            label="Max gas price"
            value={settings.maxGasGwei}
            min={0}
            max={5}
            step={0.05}
            unit="gwei"
            format={(v) => v.toFixed(2)}
            onChange={(v) => set("maxGasGwei", v)}
          />
        </Section>

        <Section title="Risk & Limits">
          <ParamSlider
            label="Max concurrent trades"
            value={settings.maxConcurrentTrades}
            min={1}
            max={20}
            step={1}
            onChange={(v) => set("maxConcurrentTrades", v)}
          />
          <ParamSlider
            label="Cooldown per network"
            value={settings.cooldownMs}
            min={0}
            max={30000}
            step={500}
            unit="ms"
            onChange={(v) => set("cooldownMs", v)}
          />
          <ParamSlider
            label="Tx deadline"
            value={settings.deadlineSec}
            min={5}
            max={120}
            step={1}
            unit="s"
            onChange={(v) => set("deadlineSec", v)}
          />
          <NumberField
            label="Daily loss limit"
            value={settings.maxDailyLossUsd}
            min={0}
            step={50}
            unit="USD"
            onCommit={(v) => set("maxDailyLossUsd", v)}
          />
          <NumberField
            label="Max position size"
            value={settings.maxPositionUsd}
            min={0}
            step={5000}
            unit="USD"
            onCommit={(v) => set("maxPositionUsd", v)}
          />
        </Section>

        <Section title="Networks">
          <div className="flex flex-wrap gap-1.5">
            {networks.map((n) => (
              <ChipToggle
                key={n.key}
                label={n.name}
                dotColor={networkColor(n.key)}
                active={settings.networks.includes(n.key)}
                onClick={() => set("networks", toggleIn(settings.networks, n.key, 1))}
              />
            ))}
          </div>
        </Section>

        <Section title="DEX Venues">
          <div className="flex flex-wrap gap-1.5">
            {availableDexes.map((d) => (
              <ChipToggle
                key={d}
                label={dexLabel(d)}
                active={settings.dexes.includes(d)}
                onClick={() => set("dexes", toggleIn(settings.dexes, d, 1))}
              />
            ))}
          </div>
        </Section>

        <Section title="Token Universe">
          <div className="flex flex-wrap gap-1.5">
            {TOKEN_UNIVERSE.map((t) => (
              <ChipToggle
                key={t}
                label={t}
                active={settings.tokens.includes(t)}
                onClick={() => set("tokens", toggleIn(settings.tokens, t, 2))}
              />
            ))}
          </div>
          <p className="mt-2 text-[11px] text-ink-faint">
            Flash-loan size ≈ {formatUsd(settings.loanAmountUsd, { compact: true })} · scanning{" "}
            {settings.networks.length} network(s), {settings.dexes.length} venue(s)
          </p>
        </Section>
      </div>
    </Card>
  );
}
