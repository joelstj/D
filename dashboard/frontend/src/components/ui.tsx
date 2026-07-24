import type { ReactNode } from "react";
import { useEffect, useState } from "react";

/* ---------------------------------------------------------------- Card --- */
export function Card({
  title,
  subtitle,
  right,
  children,
  className = "",
  bodyClassName = "",
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <section className={`card ${className}`}>
      {(title || right) && (
        <header className="flex items-start justify-between gap-3 px-5 pt-4 pb-3">
          <div>
            {title && <h2 className="text-sm font-semibold tracking-tight text-ink">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-xs text-ink-faint">{subtitle}</p>}
          </div>
          {right}
        </header>
      )}
      <div className={bodyClassName || "px-5 pb-5"}>{children}</div>
    </section>
  );
}

/* -------------------------------------------------------------- Toggle --- */
export function Toggle({
  checked,
  onChange,
  label,
  disabled,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label?: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`focusable relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors disabled:opacity-40 ${
        checked ? "bg-accent" : "bg-surface-3"
      }`}
    >
      <span
        className={`inline-block h-4.5 w-4.5 transform rounded-full bg-white shadow transition-transform ${
          checked ? "translate-x-5.5" : "translate-x-1"
        }`}
        style={{ height: 18, width: 18, transform: `translateX(${checked ? 22 : 3}px)` }}
      />
    </button>
  );
}

export function ToggleRow({
  label,
  hint,
  checked,
  onChange,
  disabled,
}: {
  label: string;
  hint?: string;
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <div className="flex items-center justify-between gap-4 py-2">
      <div>
        <div className="text-sm text-ink">{label}</div>
        {hint && <div className="text-xs text-ink-faint">{hint}</div>}
      </div>
      <Toggle checked={checked} onChange={onChange} label={label} disabled={disabled} />
    </div>
  );
}

/* --------------------------------------------------------- ParamSlider --- */
export function ParamSlider({
  label,
  value,
  min,
  max,
  step = 1,
  unit,
  format,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  unit?: string;
  format?: (v: number) => string;
  onChange: (v: number) => void;
}) {
  const pct = ((value - min) / (max - min)) * 100;
  return (
    <label className="block py-2">
      <div className="mb-1.5 flex items-baseline justify-between">
        <span className="text-sm text-ink-muted">{label}</span>
        <span className="tabular text-sm font-medium text-ink">
          {format ? format(value) : value}
          {unit ? <span className="ml-1 text-ink-faint">{unit}</span> : null}
        </span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="h-1.5 w-full cursor-pointer appearance-none rounded-full outline-none"
        style={{
          background: `linear-gradient(90deg, var(--color-accent) ${pct}%, var(--color-surface-3) ${pct}%)`,
        }}
      />
    </label>
  );
}

/* --------------------------------------------------------- NumberField --- */
export function NumberField({
  label,
  value,
  min,
  max,
  step,
  unit,
  onCommit,
}: {
  label: string;
  value: number;
  min?: number;
  max?: number;
  step?: number;
  unit?: string;
  onCommit: (v: number) => void;
}) {
  const [draft, setDraft] = useState(String(value));
  useEffect(() => setDraft(String(value)), [value]);

  const commit = () => {
    const n = Number(draft);
    if (Number.isFinite(n)) onCommit(clamp(n, min, max));
    else setDraft(String(value));
  };

  return (
    <label className="block py-2">
      <span className="mb-1.5 block text-sm text-ink-muted">{label}</span>
      <div className="flex items-center gap-2 rounded-lg border border-border bg-surface-2 px-3 focus-within:border-accent">
        <input
          type="number"
          value={draft}
          min={min}
          max={max}
          step={step}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => e.key === "Enter" && commit()}
          className="tabular w-full bg-transparent py-2 text-sm text-ink outline-none [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none"
        />
        {unit && <span className="shrink-0 text-xs text-ink-faint">{unit}</span>}
      </div>
    </label>
  );
}

/* -------------------------------------------------------------- Chips --- */
export function ChipToggle({
  label,
  active,
  onClick,
  dotColor,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
  dotColor?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`focusable inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition-colors ${
        active
          ? "border-accent bg-accent-soft text-ink"
          : "border-border bg-surface-2 text-ink-faint hover:text-ink-muted"
      }`}
    >
      {dotColor && (
        <span className="h-2 w-2 rounded-full" style={{ background: dotColor }} aria-hidden />
      )}
      {label}
    </button>
  );
}

/* ----------------------------------------------------------- Segmented --- */
export function Segmented<T extends string>({
  options,
  value,
  onChange,
}: {
  options: { value: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div className="inline-flex rounded-lg border border-border bg-surface-2 p-0.5">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          onClick={() => onChange(o.value)}
          className={`focusable rounded-md px-3 py-1 text-xs font-medium transition-colors ${
            value === o.value ? "bg-accent text-white" : "text-ink-faint hover:text-ink-muted"
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/* -------------------------------------------------------------- Badge --- */
export function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "neutral" | "pos" | "neg" | "warn" | "accent";
}) {
  const tones: Record<string, string> = {
    neutral: "border-border bg-surface-2 text-ink-muted",
    pos: "border-transparent bg-pos-soft text-pos",
    neg: "border-transparent bg-neg-soft text-neg",
    warn: "border-transparent text-warn",
    accent: "border-transparent bg-accent-soft text-accent-2",
  };
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-[11px] font-medium ${tones[tone]}`}
    >
      {children}
    </span>
  );
}

function clamp(n: number, min?: number, max?: number): number {
  if (min !== undefined) n = Math.max(min, n);
  if (max !== undefined) n = Math.min(max, n);
  return n;
}
