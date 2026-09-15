import type { ReactNode } from "react";

/** "no_answer" → "No answer", "sip" → "SIP", "+447…" left alone. */
export function humanize(value: string): string {
  const s = value.replace(/[_-]+/g, " ").trim();
  if (!s) return "—";
  if (/^(sip|sla|vip|pstn|ai|sms)$/i.test(s)) return s.toUpperCase();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export type BreakdownRow = { name: string; count: number; label?: string };

type Props = {
  title: string;
  data: Record<string, number> | BreakdownRow[];
  /** Shown as the right-hand figure instead of the raw count (e.g. minutes). */
  format?: (v: number) => string;
  empty?: ReactNode;
  hint?: ReactNode;
  /** Highest number of rows before the rest is folded into "Other". */
  max?: number;
  className?: string;
};

/** Card with a proportion bar, right-aligned figure and share per row. */
export function Breakdown({ title, data, format, empty = "No data yet", hint, max = 6, className }: Props) {
  let rows: BreakdownRow[] = Array.isArray(data)
    ? [...data]
    : Object.entries(data).map(([name, count]) => ({ name, count }));
  rows.sort((a, b) => b.count - a.count);
  if (rows.length > max) {
    const rest = rows.slice(max - 1).reduce((s, r) => s + r.count, 0);
    rows = [...rows.slice(0, max - 1), { name: "other", count: rest, label: "Other" }];
  }
  const total = rows.reduce((s, r) => s + r.count, 0);
  const top = rows[0]?.count ?? 0;
  return (
    <div className={`card breakdown${className ? ` ${className}` : ""}`}>
      <h2>{title}</h2>
      {rows.length ? (
        <ul>
          {rows.map((r) => {
            const share = total ? Math.round((r.count / total) * 100) : 0;
            return (
              <li key={r.name}>
                <span className="name" title={r.label ?? humanize(r.name)}>{r.label ?? humanize(r.name)}</span>
                <b>{format ? format(r.count) : r.count}</b>
                <em>{share}%</em>
                <i><i style={{ width: `${top ? (r.count / top) * 100 : 0}%` }} /></i>
              </li>
            );
          })}
        </ul>
      ) : (
        <div className="muted small">{empty}</div>
      )}
      {hint && <div className="hint">{hint}</div>}
    </div>
  );
}
