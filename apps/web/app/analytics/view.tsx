"use client";

import Link from "next/link";
import { useEffect, useMemo, useState, useTransition } from "react";
import { Breakdown } from "@/app/breakdown";
import { AreaLine, Donut } from "@/app/charts";
import Insights from "./insights";
import {
  type ComparisonAnalytics,
  type InsightsReport,
  type OverviewAnalytics,
  type Segment,
  type SegmentAnalytics,
  pct,
  queryAnalytics,
  secs,
  ms,
} from "@/lib/api";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const QUICK = ["This month vs last month", "Last 7 days", "Last month", "This year vs last year", "After hours this month", "Weekends vs weekdays last month"];
const PRESETS: { label: string; days: number }[] = [
  { label: "7 days", days: 7 },
  { label: "30 days", days: 30 },
  { label: "90 days", days: 90 },
];
const P1 = "var(--blue)";
const P2 = "var(--violet)";

type Mode = "overview" | "compare";

const iso = (d: Date) => d.toISOString().slice(0, 10);
const addDays = (d: string, n: number) => {
  const x = new Date(d + "T00:00:00Z");
  x.setUTCDate(x.getUTCDate() + n);
  return iso(x);
};
const spanDays = (s: Segment) => Math.round((Date.parse(s.end) - Date.parse(s.start)) / 86400000) + 1;
const previousOf = (s: Segment): Segment => {
  const n = spanDays(s);
  return { ...s, end: addDays(s.start, -1), start: addDays(s.start, -n), label: null };
};
const lastN = (n: number): Segment => {
  const end = iso(new Date());
  return { start: addDays(end, -(n - 1)), end, hours: "all", days: "all", label: `Last ${n} days` };
};
const fmt = (d: string) => new Date(d + "T00:00:00Z").toLocaleDateString("en-GB", { day: "numeric", month: "short" });
const segName = (s: Segment) => {
  const base = `${fmt(s.start)} – ${fmt(s.end)}`;
  const extra = [s.days !== "all" ? s.days : null, s.hours === "business" ? "business hrs" : s.hours === "after" ? "after hrs" : null].filter(Boolean);
  return extra.length ? `${base} · ${extra.join(", ")}` : base;
};

function Delta({ v }: { v: number | null | undefined }) {
  if (v == null) return <span className="delta muted">—</span>;
  const cls = v > 0 ? "up" : v < 0 ? "down" : "";
  return <span className={`delta ${cls}`}>{v > 0 ? "↑" : v < 0 ? "↓" : ""}{Math.abs(v).toFixed(1)}%</span>;
}

function Bars({ values, labels, tall, color = P1 }: { values: number[]; labels: string[]; tall?: boolean; color?: string }) {
  const max = Math.max(1, ...values);
  return (
    <div className={`bars ${tall ? "tall" : ""}`}>
      {values.map((v, i) => (
        <div key={i} className="bar" style={{ height: `${(v / max) * 100}%`, background: color }} title={`${labels[i]}: ${v}`}>
          <span>{labels[i]}</span>
        </div>
      ))}
    </div>
  );
}

function PairedBars({ a, b, labels }: { a: number[]; b: number[]; labels: string[] }) {
  const max = Math.max(1, ...a, ...b);
  return (
    <div className="bars tall paired">
      {labels.map((l, i) => (
        <div key={l} className="pair" title={`${l}: ${a[i]} vs ${b[i]}`}>
          <div className="bar" style={{ height: `${(a[i] / max) * 100}%`, background: P1 }} />
          <div className="bar" style={{ height: `${(b[i] / max) * 100}%`, background: P2 }} />
          <span>{l}</span>
        </div>
      ))}
    </div>
  );
}

function Legend({ a, b }: { a: Segment; b: Segment }) {
  return (
    <span className="legend small">
      <i style={{ background: P1 }} /> {segName(a)} <span className="muted">vs</span> <i style={{ background: P2 }} /> {segName(b)}
    </span>
  );
}

function Pair({ a, b, delta, fmt: f = String }: { a: number | null; b: number | null; delta?: number | null; fmt?: (v: number | null) => string }) {
  return (
    <span className="pair-vals">
      <b style={{ color: P1 }}>{f(a)}</b> <span className="muted">→</span> <b style={{ color: P2 }}>{f(b)}</b> <Delta v={delta} />
    </span>
  );
}

function SegmentPicker({ value, onChange, color, label }: { value: Segment; onChange: (s: Segment) => void; color: string; label: string }) {
  return (
    <span className="seg-picker">
      <i style={{ background: color }} />
      <span className="small muted">{label}</span>
      <input type="date" value={value.start} max={value.end} onChange={(e) => onChange({ ...value, start: e.target.value, label: null })} />
      <span className="muted">–</span>
      <input type="date" value={value.end} min={value.start} onChange={(e) => onChange({ ...value, end: e.target.value, label: null })} />
      <select value={value.hours} onChange={(e) => onChange({ ...value, hours: e.target.value as Segment["hours"] })}>
        <option value="all">All hours</option>
        <option value="business">Business hours</option>
        <option value="after">After hours</option>
      </select>
      <select value={value.days} onChange={(e) => onChange({ ...value, days: e.target.value as Segment["days"] })}>
        <option value="all">All days</option>
        <option value="weekdays">Weekdays</option>
        <option value="weekends">Weekends</option>
      </select>
    </span>
  );
}

const USAGE: { key: Exclude<keyof OverviewAnalytics["usage"], "month">; label: string; round?: boolean }[] = [
  { key: "calls", label: "Calls" },
  { key: "minutes", label: "Minutes", round: true },
  { key: "transfers", label: "Transfers" },
  { key: "tickets", label: "Tickets" },
  { key: "bookings", label: "Bookings" },
  { key: "sms", label: "SMS sent" },
  { key: "whatsapp", label: "WhatsApp" },
  { key: "web_chats", label: "Web chats" },
  { key: "emails", label: "Emails" },
];

export default function AnalyticsView({ overview, initial, insights }: { overview: OverviewAnalytics; initial: ComparisonAnalytics; insights: InsightsReport | null }) {
  const [mode, setMode] = useState<Mode>("overview");
  const [data, setData] = useState<ComparisonAnalytics>(initial);
  const [p1, setP1] = useState<Segment>(initial.current.segment);
  const [p2, setP2] = useState<Segment>(initial.compare?.segment ?? previousOf(initial.current.segment));
  const [question, setQuestion] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, start] = useTransition();
  const [dirty, setDirty] = useState(false);

  const run = (body: Parameters<typeof queryAnalytics>[0]) =>
    start(async () => {
      setError(null);
      const r = await queryAnalytics({ timezone: overview.timezone, ...body });
      if (!r.ok) return setError(r.error);
      setData(r.data);
      setP1(r.data.current.segment);
      setP2(r.data.compare?.segment ?? previousOf(r.data.current.segment));
      if (r.data.compare && body.question) setMode("compare");
      setDirty(false);
    });

  useEffect(() => {
    if (!dirty) return;
    const t = setTimeout(() => run({ period: p1, compare: mode === "compare" ? p2 : previousOf(p1) }), 400);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p1, p2, dirty, mode]);

  const cur = data.current;
  const cmp: SegmentAnalytics = data.compare ?? initial.compare ?? data.current;
  const c = cur.summary, p = cmp.summary;
  const total = cur.first_time_callers + cur.returning_callers;
  const dayLabels = useMemo(() => cur.daily.map((d) => fmt(d.day)), [cur.daily]);

  const setSeg = (which: 1 | 2) => (s: Segment) => { (which === 1 ? setP1 : setP2)(s); setDirty(true); };

  return (
    <>
      <div className="an-head">
        <div>
          <h1 style={{ marginBottom: 2 }}>Analytics</h1>
          <p className="muted small" style={{ margin: 0 }}>Performance insights &amp; trends · {overview.timezone}</p>
        </div>
        <div className="toggle">
          <button className={mode === "overview" ? "active" : ""} onClick={() => setMode("overview")}>Overview</button>
          <button className={mode === "compare" ? "active" : ""} onClick={() => { setMode("compare"); if (!data.compare) setDirty(true); }}>Compare{data.compare ? " •" : ""}</button>
        </div>
      </div>

      <form className="ask card" onSubmit={(e) => { e.preventDefault(); if (question.trim()) run({ question }); }}>
        <div className="ask-row">
          <span className="ask-badge">✦ Ask AI</span>
          <input
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder='Ask about your calls, e.g. "this month vs last month" or "after hours calls in August"'
          />
          <button className="primary" type="submit" disabled={pending || !question.trim()}>{pending ? "…" : "Ask"}</button>
        </div>
        <div className="chips small" style={{ marginTop: 6 }}>
          <span className="muted">Quick:</span>
          {QUICK.map((q) => (
            <a key={q} href="#" onClick={(e) => { e.preventDefault(); setQuestion(q); run({ question: q }); }}>{q}</a>
          ))}
        </div>
        {data.question?.interpretation && (
          <p className="small muted" style={{ margin: "6px 0 0" }}>
            Showing: {data.question.interpretation}
            {data.question.source === "llm" ? " · AI" : ""}
          </p>
        )}
        {error && <p className="small" style={{ color: "var(--bad-fg)", margin: "6px 0 0" }}>{error}</p>}
      </form>

      <div className="period card">
        <span className="small muted">Period:</span>
        <SegmentPicker value={p1} onChange={setSeg(1)} color={P1} label="P1" />
        {mode === "compare" && (
          <>
            <span className="small muted">vs</span>
            <SegmentPicker value={p2} onChange={setSeg(2)} color={P2} label="P2" />
          </>
        )}
        <span className="chips" style={{ marginLeft: "auto" }}>
          {PRESETS.map((pr) => (
            <a key={pr.days} href="#" className={spanDays(p1) === pr.days && p1.hours === "all" && p1.days === "all" ? "active" : ""} onClick={(e) => { e.preventDefault(); const s = lastN(pr.days); setP1(s); setP2(previousOf(s)); setDirty(true); }}>{pr.label}</a>
          ))}
        </span>
        {pending && <span className="small muted">updating…</span>}
      </div>

      {mode === "overview" ? (
        <>
          <div className="grid kpis">
            <div className="card"><div className="label">Total calls</div><div className="value">{c.total_calls}<Delta v={data.change.total_calls} /></div><div className="small muted">prev {p.total_calls}</div></div>
            <div className="card"><div className="label">Answer rate</div><div className="value">{pct(c.answer_rate)}<Delta v={data.change.answer_rate} /></div><div className="small muted">{c.missed} missed · {c.blocked} blocked</div></div>
            <div className="card"><div className="label">Avg duration</div><div className="value">{secs(c.avg_duration_s)}<Delta v={data.change.avg_duration_s} /></div><div className="small muted">{Math.round(c.total_minutes)} min total</div></div>
            <div className="card"><div className="label">Avg pick-up</div><div className="value">{ms(c.avg_answer_latency_s)}</div></div>
            <div className="card"><div className="label">Unique callers</div><div className="value">{c.unique_callers}<Delta v={data.change.unique_callers} /></div><div className="small muted">{c.avg_calls_per_caller ?? "—"} calls / caller</div></div>
            <div className="card"><div className="label">New callers</div><div className="value">{c.new_callers}<Delta v={data.change.new_callers} /></div></div>
            <div className="card"><div className="label">After hours</div><div className="value">{cur.after_hours_calls}<Delta v={data.change.after_hours_calls} /></div><div className="small muted">{cur.business_hours_calls} in business hours</div></div>
            <div className="card"><div className="label">Transfers</div><div className="value">{cur.transfers_total}<Delta v={data.change.transfers_total} /></div><div className="small muted">{cur.transfers_answered} reached a human · {cur.tickets} tickets</div></div>
          </div>

          <div className="grid" style={{ gridTemplateColumns: "2fr 1fr" }}>
            <div className="card">
              <h2>Call volume</h2>
              <AreaLine id="vol" series={[{ values: cur.daily.map((d) => d.calls), color: P1, label: "Calls" }]} labels={dayLabels} />
            </div>
            <div className="card">
              <h2>Day of week</h2>
              <Bars tall values={cur.by_weekday} labels={DAYS} />
            </div>
          </div>

          <div className="grid" style={{ gridTemplateColumns: "1fr 1fr 1fr" }}>
            <div className="card">
              <h2>Calls by department</h2>
              <Donut
                data={cur.by_department}
                total="Calls"
                empty={<>No department routing in this period. Departments and their staff are set up on the <a href="/handoff">Transfers</a> page; calls appear here once the assistant routes or transfers to one.</>}
              />
            </div>
            <div className="card">
              <h2>First-time vs returning</h2>
              <div className="split">
                <div style={{ width: `${total ? (cur.first_time_callers / total) * 100 : 50}%`, background: P1 }} />
                <div style={{ flex: 1, background: P2 }} />
              </div>
              <dl className="kv stats">
                <dt><i className="dot" style={{ background: P1 }} /> First-time</dt><dd>{cur.first_time_callers} ({total ? Math.round((cur.first_time_callers / total) * 100) : 0}%)</dd>
                <dt><i className="dot" style={{ background: P2 }} /> Returning</dt><dd>{cur.returning_callers} ({total ? Math.round((cur.returning_callers / total) * 100) : 0}%)</dd>
                <dt>Prospects / customers</dt><dd>{overview.prospects.prospects} / {overview.prospects.customers}</dd>
                <dt>VIP</dt><dd>{overview.prospects.vip}</dd>
              </dl>
            </div>
            <div className="card">
              <h2>Volume by hour</h2>
              <Bars values={cur.by_hour} labels={cur.by_hour.map((_, h) => (h % 3 === 0 ? String(h).padStart(2, "0") : ""))} />
            </div>
          </div>

          <div className="grid">
            <Breakdown title="Outcomes" data={cur.by_outcome} empty="No calls in this period." />
            <div className="card">
              <h2>Usage this month <span className="sub">{new Date(overview.usage.month + "-01T00:00:00Z").toLocaleDateString("en-GB", { month: "long", year: "numeric" })} · every channel</span></h2>
              <div className="usage-grid">
                {USAGE.map((u) => (
                  <div key={u.key}>
                    <div className="label">{u.label}</div>
                    <div className="value">{u.round ? Math.round(overview.usage[u.key]) : overview.usage[u.key]}</div>
                  </div>
                ))}
              </div>
            </div>
            <Breakdown
              title="Information not captured"
              data={overview.top_missed_fields.map((m) => ({ name: m.field, count: m.count }))}
              empty="The assistant captured every required detail."
              hint="Required details callers didn't give — tune the wording in Studio → Required fields."
            />
            <Breakdown
              title="Feedback flags"
              data={overview.feedback_by_type}
              empty="No feedback flagged on calls in this period."
            />
            <div className="card">
              <h2>Handoff</h2>
              <dl className="kv stats">
                <dt>Transfers</dt><dd>{overview.transfers.total}</dd>
                <dt>Tickets open</dt><dd>{overview.tickets.open}</dd>
                <dt>SLA breached</dt><dd>{overview.tickets.sla_breached}</dd>
                <dt>Time to claim</dt><dd>{secs(overview.tickets.avg_time_to_claim_s)}</dd>
                <dt>Time to resolve</dt><dd>{secs(overview.tickets.avg_time_to_resolve_s)}</dd>
              </dl>
              <p className="small"><Link href="/handoff">Full handoff analytics →</Link></p>
            </div>
          </div>

          <Insights initial={insights} timezone={overview.timezone} />
        </>
      ) : (
        <>
          <div className="card" style={{ marginBottom: "1rem" }}>
            <div className="an-head" style={{ marginBottom: "0.6rem" }}>
              <h2 style={{ margin: 0 }}>Period comparison</h2>
              <Legend a={cur.segment} b={cmp.segment} />
            </div>
            <div className="grid" style={{ marginBottom: 0 }}>
              <div className="card cmp"><div className="label">Total calls</div><Pair a={c.total_calls} b={p.total_calls} delta={data.change.total_calls} /></div>
              <div className="card cmp"><div className="label">Avg duration</div><Pair a={c.avg_duration_s} b={p.avg_duration_s} delta={data.change.avg_duration_s} fmt={secs} /></div>
              <div className="card cmp"><div className="label">New callers</div><Pair a={c.new_callers} b={p.new_callers} delta={data.change.new_callers} /></div>
              <div className="card cmp"><div className="label">Answer rate</div><Pair a={c.answer_rate} b={p.answer_rate} delta={data.change.answer_rate} fmt={pct} /></div>
            </div>
          </div>

          <div className="grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
            <div className="card">
              <div className="an-head" style={{ marginBottom: 4 }}><h2 style={{ margin: 0 }}>Daily trend comparison</h2><span className="legend small"><i style={{ background: P1 }} /> Period 1 <i style={{ background: P2 }} /> Period 2</span></div>
              <AreaLine
                id="cmp"
                series={[{ values: cur.daily.map((d) => d.calls), color: P1, label: "Period 1" }, { values: cmp.daily.map((d) => d.calls), color: P2, label: "Period 2" }]}
                labels={cur.daily.map((_, i) => `Day ${i + 1}`)}
              />
            </div>
            <div className="card">
              <h2>Day of week comparison</h2>
              <PairedBars a={cur.by_weekday} b={cmp.by_weekday} labels={DAYS} />
            </div>
          </div>

          <div className="grid" style={{ gridTemplateColumns: "1fr 1fr 1fr" }}>
            <div className="card">
              <h2>Call metrics</h2>
              <table className="cmp-table">
                <tbody>
                  <tr><td>Total calls</td><td><Pair a={c.total_calls} b={p.total_calls} delta={data.change.total_calls} /></td></tr>
                  <tr><td>Answered</td><td><Pair a={c.answered} b={p.answered} /></td></tr>
                  <tr><td>Missed</td><td><Pair a={c.missed} b={p.missed} /></td></tr>
                  <tr><td>Avg duration</td><td><Pair a={c.avg_duration_s} b={p.avg_duration_s} delta={data.change.avg_duration_s} fmt={secs} /></td></tr>
                  <tr><td>Total minutes</td><td><Pair a={Math.round(c.total_minutes)} b={Math.round(p.total_minutes)} /></td></tr>
                  <tr><td>After hours</td><td><Pair a={cur.after_hours_calls} b={cmp.after_hours_calls} delta={data.change.after_hours_calls} /></td></tr>
                </tbody>
              </table>
            </div>
            <div className="card">
              <h2>Transfer metrics</h2>
              <table className="cmp-table">
                <tbody>
                  <tr><td>Total transfers</td><td><Pair a={cur.transfers_total} b={cmp.transfers_total} delta={data.change.transfers_total} /></td></tr>
                  <tr><td>Reached a human</td><td><Pair a={cur.transfers_answered} b={cmp.transfers_answered} /></td></tr>
                  <tr><td>Tickets</td><td><Pair a={cur.tickets} b={cmp.tickets} /></td></tr>
                  <tr><td>Escalated</td><td><Pair a={c.escalated} b={p.escalated} /></td></tr>
                </tbody>
              </table>
            </div>
            <div className="card">
              <h2>Prospect metrics</h2>
              <table className="cmp-table">
                <tbody>
                  <tr><td>Unique callers</td><td><Pair a={c.unique_callers} b={p.unique_callers} delta={data.change.unique_callers} /></td></tr>
                  <tr><td>First-time</td><td><Pair a={cur.first_time_callers} b={cmp.first_time_callers} /></td></tr>
                  <tr><td>Returning</td><td><Pair a={cur.returning_callers} b={cmp.returning_callers} /></td></tr>
                  <tr><td>Calls / caller</td><td><Pair a={c.avg_calls_per_caller} b={p.avg_calls_per_caller} fmt={(v) => (v == null ? "—" : String(v))} /></td></tr>
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </>
  );
}
