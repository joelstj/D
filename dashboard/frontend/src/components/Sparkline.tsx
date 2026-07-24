import { useId } from "react";

/**
 * Minimal single-series sparkline. One hue, 2px line, soft area fill — no axes
 * or legend (the tile title names the series). Renders nothing meaningful below
 * two points.
 */
export function Sparkline({
  values,
  width = 220,
  height = 44,
  color = "var(--color-accent-2)",
}: {
  values: number[];
  width?: number;
  height?: number;
  color?: string;
}) {
  const id = useId();
  if (values.length < 2) {
    return <div style={{ width, height }} aria-hidden />;
  }

  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const pad = 3;
  const stepX = (width - pad * 2) / (values.length - 1);

  const pts = values.map((v, i) => {
    const x = pad + i * stepX;
    const y = pad + (1 - (v - min) / span) * (height - pad * 2);
    return [x, y] as const;
  });

  const line = pts.map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const area = `${line} L${pts[pts.length - 1]![0].toFixed(1)},${height} L${pts[0]![0].toFixed(1)},${height} Z`;

  return (
    <svg width={width} height={height} role="img" aria-label="trend" className="overflow-visible">
      <defs>
        <linearGradient id={`spark-${id}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.28" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#spark-${id})`} />
      <path d={line} fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={pts[pts.length - 1]![0]} cy={pts[pts.length - 1]![1]} r="2.5" fill={color} />
    </svg>
  );
}
