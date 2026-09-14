import Link from "next/link";
import type { SeriesPoint, SubscriptionStatus, TenantHealth } from "@/lib/api";

export const pct = (v: number | null | undefined) => (v == null ? "—" : `${v.toFixed(1)}%`);
export const num = (v: number | null | undefined, dp = 0) => (v == null ? "—" : v.toLocaleString("en-GB", { maximumFractionDigits: dp }));
export const secs = (v: number | null | undefined) => (v == null ? "—" : `${v.toFixed(2)}s`);
export const millis = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v)} ms`);

export function Stat({ label, value, sub }: { label: string; value: React.ReactNode; sub?: React.ReactNode }) {
  return (
    <div className="card">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {sub && <div className="small muted">{sub}</div>}
    </div>
  );
}

export function Bars({ points, tall, fmt }: { points: SeriesPoint[]; tall?: boolean; fmt?: (v: number) => string }) {
  if (!points.length) return <p className="muted small">No data yet.</p>;
  const max = Math.max(1, ...points.map((p) => p.value));
  const step = Math.max(1, Math.ceil(points.length / 12));
  return (
    <div className={`bars${tall ? " tall" : ""}`} style={{ marginBottom: "1.4rem" }}>
      {points.map((p, i) => (
        <div key={p.key} className="bar" style={{ height: `${(p.value / max) * 100}%` }} title={`${p.key}: ${fmt ? fmt(p.value) : p.value}`}>
          {i % step === 0 && <span>{p.key.length > 5 ? p.key.slice(5) : p.key}</span>}
        </div>
      ))}
    </div>
  );
}

export function Mix({ data, total }: { data: Record<string, number>; total?: number }) {
  const entries = Object.entries(data).sort((a, b) => b[1] - a[1]);
  const sum = total ?? entries.reduce((a, [, v]) => a + v, 0);
  if (!entries.length) return <p className="muted small">No data yet.</p>;
  return (
    <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
      {entries.map(([k, v]) => (
        <li key={k} className="row between small">
          <span>{k}</span>
          <span>{v} <span className="muted">({sum ? Math.round((v / sum) * 100) : 0}%)</span></span>
        </li>
      ))}
    </ul>
  );
}

export function StatusPill({ s }: { s: SubscriptionStatus }) {
  const cls = s === "active" ? "ok" : s === "trialing" ? "warn" : s === "paused" ? "" : "bad";
  return <span className={`pill ${cls}`}>{s.replace("_", " ")}</span>;
}

export function HealthPill({ h }: { h: TenantHealth }) {
  const cls = h === "healthy" ? "ok" : h === "watch" ? "warn" : h === "at_risk" ? "bad" : "";
  return <span className={`pill ${cls}`}>{h.replace("_", " ")}</span>;
}

export function TenantLink({ id, name }: { id: string; name?: string }) {
  return <Link href={`/admin/tenants/${id}`}>{name ?? id}</Link>;
}
