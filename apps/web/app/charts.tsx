"use client";

import type { ReactNode } from "react";
import { humanize } from "@/app/breakdown";

/** Premium chart primitives (pure SVG/CSS, no chart library). Shared by Analytics, Transfers, Value. */

export const PALETTE = ["#3b7bff", "#6a3df5", "#12b5a5", "#f59e0b", "#ef4444", "#ec4899", "#84cc16", "#0ea5e9", "#a855f7", "#64748b"];
export const colorAt = (i: number) => PALETTE[i % PALETTE.length];

const fmtNum = (v: number) => (Number.isInteger(v) ? String(v) : v.toFixed(1));

// -- smooth area line ------------------------------------------------------------------------------

/** Catmull-Rom → cubic Bézier so the line glides through every point without overshooting flat runs. */
function smoothPath(pts: [number, number][]): string {
  if (pts.length < 2) return pts.length ? `M${pts[0][0]},${pts[0][1]}` : "";
  let d = `M${pts[0][0]},${pts[0][1]}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i - 1] ?? pts[i], p1 = pts[i], p2 = pts[i + 1], p3 = pts[i + 2] ?? p2;
    const c1x = p1[0] + (p2[0] - p0[0]) / 6, c1y = p1[1] + (p2[1] - p0[1]) / 6;
    const c2x = p2[0] - (p3[0] - p1[0]) / 6, c2y = p2[1] - (p3[1] - p1[1]) / 6;
    d += ` C${c1x.toFixed(1)},${c1y.toFixed(1)} ${c2x.toFixed(1)},${c2y.toFixed(1)} ${p2[0]},${p2[1]}`;
  }
  return d;
}

export type Series = { values: number[]; color?: string; label?: string; dashed?: boolean };

export function AreaLine({ series, labels, height = 180, format = fmtNum, id = "al" }: { series: Series[]; labels: string[]; height?: number; format?: (v: number) => string; id?: string }) {
  const w = 600, h = height, padL = 34, padR = 12, padT = 12, padB = 24;
  const n = Math.max(2, ...series.map((s) => s.values.length));
  const max = Math.max(1, ...series.flatMap((s) => s.values));
  const x = (i: number) => padL + (i * (w - padL - padR)) / (n - 1);
  const y = (v: number) => padT + (h - padT - padB) * (1 - v / max);
  const step = Math.max(1, Math.ceil(n / 8));
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="chart area" preserveAspectRatio="none" role="img">
      <defs>
        {series.map((s, k) => (
          <linearGradient key={k} id={`${id}-g${k}`} x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor={s.color ?? colorAt(k)} stopOpacity={0.28} />
            <stop offset="100%" stopColor={s.color ?? colorAt(k)} stopOpacity={0.02} />
          </linearGradient>
        ))}
      </defs>
      {[0, 0.25, 0.5, 0.75, 1].map((t) => (
        <g key={t}>
          <line x1={padL} x2={w - padR} y1={y(max * t)} y2={y(max * t)} stroke="var(--line)" strokeDasharray={t ? "3 4" : undefined} />
          <text x={padL - 6} y={y(max * t) + 3.5} fontSize="9.5" fill="var(--muted)" textAnchor="end">{format(Math.round(max * t * 10) / 10)}</text>
        </g>
      ))}
      {series.map((s, k) => {
        const pts = s.values.map((v, i) => [x(i), y(v)] as [number, number]);
        const line = smoothPath(pts);
        const color = s.color ?? colorAt(k);
        return (
          <g key={k}>
            <path d={`${line} L${x(s.values.length - 1)},${y(0)} L${x(0)},${y(0)} Z`} fill={`url(#${id}-g${k})`} />
            <path d={line} fill="none" stroke={color} strokeWidth={2.4} strokeLinejoin="round" strokeLinecap="round" strokeDasharray={s.dashed ? "5 4" : undefined} />
            {s.values.map((v, i) => (
              <circle key={i} cx={x(i)} cy={y(v)} r={n > 40 ? 0 : 3} fill="var(--panel)" stroke={color} strokeWidth={1.8}>
                <title>{`${labels[i] ?? ""}${s.label ? ` · ${s.label}` : ""}: ${format(v)}`}</title>
              </circle>
            ))}
          </g>
        );
      })}
      {labels.map((l, i) => (i % step === 0 || i === labels.length - 1 ? <text key={i} x={x(i)} y={h - 7} fontSize="9.5" fill="var(--muted)" textAnchor={i === 0 ? "start" : i === labels.length - 1 ? "end" : "middle"}>{l}</text> : null))}
    </svg>
  );
}

// -- donut -----------------------------------------------------------------------------------------

export type DonutRow = { name: string; count: number; label?: string };

export function Donut({ data, total: totalLabel = "Total", format = String, max = 7, empty = "No data yet", colors }: { data: Record<string, number> | DonutRow[]; total?: string; format?: (v: number) => string; max?: number; empty?: ReactNode; colors?: string[] }) {
  let rows: DonutRow[] = (Array.isArray(data) ? [...data] : Object.entries(data).map(([name, count]) => ({ name, count }))).filter((r) => r.count > 0);
  rows.sort((a, b) => b.count - a.count);
  if (rows.length > max) {
    const rest = rows.slice(max - 1).reduce((s, r) => s + r.count, 0);
    rows = [...rows.slice(0, max - 1), { name: "other", count: rest, label: "Other" }];
  }
  const total = rows.reduce((s, r) => s + r.count, 0);
  if (!total) return <div className="muted small">{empty}</div>;
  const R = 42, C = 2 * Math.PI * R;
  let offset = 0;
  return (
    <div className="donut">
      <svg viewBox="0 0 100 100" role="img">
        <circle cx="50" cy="50" r={R} fill="none" stroke="var(--hover)" strokeWidth="14" />
        {rows.map((r, i) => {
          const len = (r.count / total) * C;
          const el = (
            <circle key={r.name} cx="50" cy="50" r={R} fill="none" stroke={colors?.[i] ?? colorAt(i)} strokeWidth="14"
              strokeDasharray={`${Math.max(0, len - 1.2)} ${C - Math.max(0, len - 1.2)}`} strokeDashoffset={-offset} transform="rotate(-90 50 50)" strokeLinecap="butt">
              <title>{`${r.label ?? humanize(r.name)}: ${format(r.count)} (${Math.round((r.count / total) * 100)}%)`}</title>
            </circle>
          );
          offset += len;
          return el;
        })}
        <text x="50" y="47" textAnchor="middle" fontSize="15" fontWeight="700" fill="var(--fg)">{format(total)}</text>
        <text x="50" y="59" textAnchor="middle" fontSize="7" fill="var(--muted)" style={{ textTransform: "uppercase", letterSpacing: ".06em" }}>{totalLabel}</text>
      </svg>
      <ul>
        {rows.map((r, i) => (
          <li key={r.name}>
            <i style={{ background: colors?.[i] ?? colorAt(i) }} />
            <span className="name" title={r.label ?? humanize(r.name)}>{r.label ?? humanize(r.name)}</span>
            <b>{format(r.count)}</b>
            <em>{Math.round((r.count / total) * 100)}%</em>
          </li>
        ))}
      </ul>
    </div>
  );
}

// -- heat-map --------------------------------------------------------------------------------------

const WD = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export function Heatmap({ grid, format = fmtNum, unit = "calls" }: { grid: number[][]; format?: (v: number) => string; unit?: string }) {
  const max = Math.max(1, ...grid.flat());
  return (
    <div className="heatmap" role="img" aria-label={`${unit} by weekday and hour`}>
      <div className="hm-corner" />
      {Array.from({ length: 24 }, (_, h) => (
        <div key={h} className="hm-h">{h % 3 === 0 ? String(h).padStart(2, "0") : ""}</div>
      ))}
      {grid.map((row, d) => (
        <div key={d} className="hm-row">
          <div className="hm-d">{WD[d]}</div>
          {row.map((v, h) => (
            <div key={h} className="hm-cell" style={{ opacity: v ? 0.18 + 0.82 * (v / max) : 1, background: v ? "var(--blue)" : "var(--hover)" }} title={`${WD[d]} ${String(h).padStart(2, "0")}:00 · ${format(v)} ${unit}`} />
          ))}
        </div>
      ))}
    </div>
  );
}

// -- funnel ----------------------------------------------------------------------------------------

export function Funnel({ steps, colors }: { steps: { label: string; count: number; share?: number | null }[]; colors?: string[] }) {
  const top = steps[0]?.count ?? 0;
  if (!top) return <div className="muted small">No answered calls in this period.</div>;
  return (
    <ol className="funnel">
      {steps.map((s, i) => {
        const w = top ? Math.max(4, (s.count / top) * 100) : 0;
        const share = s.share ?? (top ? s.count / top : 0);
        return (
          <li key={s.label}>
            <span className="name">{s.label}</span>
            <span className="track"><i style={{ width: `${w}%`, background: colors?.[i] ?? `linear-gradient(90deg, ${colorAt(0)}, ${colorAt(1)})`, opacity: 1 - i * 0.09 }} /></span>
            <b>{s.count}</b>
            <em>{Math.round(share * 100)}%</em>
          </li>
        );
      })}
    </ol>
  );
}

// -- vertical bars (gradient) ---------------------------------------------------------------------

export function Columns({ values, labels, color, tall, format = fmtNum, highlight }: { values: number[]; labels: string[]; color?: string; tall?: boolean; format?: (v: number) => string; highlight?: (i: number) => boolean }) {
  const max = Math.max(1, ...values);
  return (
    <div className={`bars ${tall ? "tall" : ""} premium`}>
      {values.map((v, i) => (
        <div key={i} className={`bar${highlight?.(i) ? " hot" : ""}`} style={{ height: `${Math.max(1.5, (v / max) * 100)}%`, background: color }} title={`${labels[i]}: ${format(v)}`}>
          <span>{labels[i]}</span>
        </div>
      ))}
    </div>
  );
}

// -- horizontal ranked bars (label / bar / value) -------------------------------------------------

export function RankedBars({ rows, format = fmtNum, empty = "No data yet", color }: { rows: { label: string; value: number; sub?: string; color?: string }[]; format?: (v: number) => string; empty?: ReactNode; color?: string }) {
  if (!rows.length) return <div className="muted small">{empty}</div>;
  const top = Math.max(...rows.map((r) => Math.abs(r.value)), 1e-9);
  return (
    <ul className="ranked">
      {rows.map((r, i) => (
        <li key={`${r.label}-${i}`}>
          <span className="name" title={r.label}>{r.label}{r.sub && <small className="muted"> · {r.sub}</small>}</span>
          <b>{format(r.value)}</b>
          <i><i style={{ width: `${(Math.abs(r.value) / top) * 100}%`, background: r.color ?? color ?? colorAt(i) }} /></i>
        </li>
      ))}
    </ul>
  );
}

// -- metric tile -----------------------------------------------------------------------------------

export function Metric({ label, value, sub, tone }: { label: string; value: ReactNode; sub?: ReactNode; tone?: "good" | "warn" | "bad" }) {
  return (
    <div className="card metric">
      <div className="label">{label}</div>
      <div className={`value${tone ? ` ${tone}` : ""}`}>{value}</div>
      {sub && <div className="small muted">{sub}</div>}
    </div>
  );
}
