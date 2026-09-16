"use client";

import Link from "next/link";
import { useEffect, useState, useTransition } from "react";
import { Breakdown } from "@/app/breakdown";
import { AreaLine, Columns, Donut, Funnel, Heatmap, Metric, RankedBars, colorAt } from "@/app/charts";
import { type InsightsReport, type Slice, type TrendPoint, downloadInsightsCsv, fetchInsights, pct, secs } from "@/lib/api";
import Advisor from "./advisor";
import Reports from "./reports";

export type InsightTab = "demand" | "trends" | "resolution" | "team" | "customers" | "revenue" | "advisor" | "reports";
const EXPORT_SECTIONS: Partial<Record<InsightTab, string[]>> = {
  demand: ["demand", "forecast"],
  trends: ["trends"],
  resolution: ["resolution", "sla", "transfers"],
  team: ["workforce", "cost"],
  customers: ["cx", "intents", "gaps"],
  revenue: ["revenue", "attribution"],
};
const TABS: { key: InsightTab; label: string; blurb: string }[] = [
  { key: "demand", label: "Demand", blurb: "When callers ring, what the assistant absorbs, and what next week looks like." },
  { key: "trends", label: "Trends", blurb: "Long-range patterns by hour, weekday, month and year." },
  { key: "resolution", label: "Resolution", blurb: "Where calls get resolved, ticket & callback SLAs, transfer quality." },
  { key: "team", label: "Team", blurb: "Who's picking up tickets, callbacks and transfers — and cost vs the assistant." },
  { key: "customers", label: "Customers", blurb: "Experience, friction signals and what callers are asking for." },
  { key: "revenue", label: "Revenue", blurb: "Leads, bookings, value won and lost, and which channels bring them." },
  { key: "advisor", label: "Advisor", blurb: "AI business advisor — evidence-backed recommendations from your own data, with one-click fixes." },
  { key: "reports", label: "Reports & export", blurb: "Scheduled email reports and CSV exports of every section." },
];
const WINDOWS = [30, 90, 180, 365, 730];
const HOURS = Array.from({ length: 24 }, (_, h) => (h % 3 === 0 ? String(h).padStart(2, "0") : ""));
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

const gbp = (pence: number, currency = "GBP") =>
  new Intl.NumberFormat("en-GB", { style: "currency", currency, maximumFractionDigits: 0 }).format(pence / 100);
const hrs = (h: number | null | undefined) => (h == null ? "—" : h < 1 ? `${Math.round(h * 60)} min` : h < 48 ? `${h.toFixed(1)} h` : `${(h / 24).toFixed(1)} d`);
const day = (s: string) => (/^\d{4}-\d{2}-\d{2}$/.test(s) ? new Date(s + "T00:00:00Z").toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" }) : s);
const n1 = (v: number) => (Number.isInteger(v) ? String(v) : v.toFixed(1));
const signed = (v: number | null | undefined, suffix = "%") => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(0)}${suffix}`);
const sliceRows = (s: Slice[], pick: (x: Slice) => number = (x) => x.count) => s.map((x) => ({ label: x.label, value: pick(x) }));

function Card({ title, sub, children, wide, className }: { title: string; sub?: string; children: React.ReactNode; wide?: boolean; className?: string }) {
  return (
    <div className={`card${className ? ` ${className}` : ""}`} style={wide ? { gridColumn: "1 / -1" } : undefined}>
      <h2>{title}{sub && <span className="sub">{sub}</span>}</h2>
      {children}
    </div>
  );
}

function TrendTable({ points, unit }: { points: TrendPoint[]; unit: string }) {
  if (!points.length) return <div className="muted small">No calls in this window yet.</div>;
  return (
    <table className="team-table">
      <thead><tr><th>{unit}</th><th>Calls</th><th>Answered</th><th>Missed</th><th>Transfers</th><th>Tickets</th><th>Bookings</th><th>Answer rate</th><th>Est. value</th></tr></thead>
      <tbody>
        {[...points].reverse().map((p) => (
          <tr key={p.period}><td>{p.label}</td><td>{p.calls}</td><td>{p.answered}</td><td>{p.missed}</td><td>{p.transfers}</td><td>{p.tickets}</td><td>{p.bookings}</td><td>{pct(p.answer_rate)}</td><td>{gbp(p.est_value_pence)}</td></tr>
        ))}
      </tbody>
    </table>
  );
}

export default function Insights({ initial, timezone }: { initial: InsightsReport | null; timezone: string }) {
  const [tab, setTab] = useState<InsightTab>("demand");
  const [days, setDays] = useState(initial?.days ?? 30);
  const [data, setData] = useState<InsightsReport | null>(initial);
  const [error, setError] = useState<string | null>(initial ? null : "Insights unavailable");
  const [pending, start] = useTransition();

  useEffect(() => {
    if (data && data.days === days) return;
    start(async () => {
      const r = await fetchInsights({ days, timezone });
      if (r.ok) { setData(r.data); setError(null); } else setError(r.error);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [days]);

  const meta = TABS.find((t) => t.key === tab)!;
  const exportable = EXPORT_SECTIONS[tab] ?? [];
  return (
    <section className="insights">
      <div className="an-head" style={{ marginTop: "0.5rem" }}>
        <div>
          <h2 style={{ margin: 0, fontSize: "1.15rem" }}>Insights</h2>
          <p className="muted small" style={{ margin: "2px 0 0" }}>{meta.blurb}</p>
        </div>
        <span className="chips">
          <span className="small muted" style={{ marginRight: 6 }}>Window:</span>
          {WINDOWS.map((w) => (
            <a key={w} href="#" className={days === w ? "active" : ""} onClick={(e) => { e.preventDefault(); setDays(w); }}>{w < 365 ? `${w} days` : w === 365 ? "1 year" : "2 years"}</a>
          ))}
          {pending && <span className="small muted">updating…</span>}
          {exportable.length > 0 && (
            <button type="button" className="ghost small" style={{ marginLeft: 8 }} title={`Download ${exportable.join(", ")} as CSV`} onClick={() => exportable.forEach((sec) => void downloadInsightsCsv(sec, { days, timezone }))}>
              Export CSV
            </button>
          )}
        </span>
      </div>
      <div className="insights-tabs" role="tablist">
        {TABS.map((t) => (
          <button key={t.key} role="tab" aria-selected={tab === t.key} className={tab === t.key ? "active" : ""} onClick={() => setTab(t.key)}>{t.label}</button>
        ))}
      </div>
      {tab === "advisor" && <Advisor />}
      {tab === "reports" && <Reports days={days} timezone={timezone} />}
      {error && !data && tab !== "advisor" && tab !== "reports" && <div className="card muted small">{error}</div>}
      {data && tab !== "advisor" && tab !== "reports" && (
        <div style={{ opacity: pending ? 0.6 : 1, transition: "opacity .2s" }}>
          {tab === "demand" && <DemandTab d={data} />}
          {tab === "trends" && <TrendsTab d={data} />}
          {tab === "resolution" && <ResolutionTab d={data} />}
          {tab === "team" && <TeamTab d={data} />}
          {tab === "customers" && <CustomersTab d={data} />}
          {tab === "revenue" && <RevenueTab d={data} />}
        </div>
      )}
    </section>
  );
}

// -- Demand -----------------------------------------------------------------------------------------

function DemandTab({ d }: { d: InsightsReport }) {
  const dm = d.demand;
  const fc = dm.forecast;
  return (
    <>
      <div className="grid kpis">
        <Metric label="Next 7 days" value={Math.round(dm.forecast_total)} sub={<>expected calls · {signed(dm.forecast_vs_last_week_pct)} vs last week</>} />
        <Metric label="Would have been missed" value={dm.would_have_missed_total} sub="after hours or while another call was live" tone={dm.would_have_missed_total ? "good" : undefined} />
        <Metric label="After hours" value={dm.after_hours_calls} sub="calls outside opening hours" />
        <Metric label="Peak concurrency" value={dm.peak_concurrency} sub={dm.peak_concurrency_at ? `at ${new Date(dm.peak_concurrency_at).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" })}` : "simultaneous calls"} />
      </div>
      <div className="grid" style={{ gridTemplateColumns: "1fr" }}>
        <Card title="When callers ring" sub={`Calls by weekday × hour of day · last ${d.days} days · ${d.timezone}`}>
          <Heatmap grid={dm.heatmap} />
        </Card>
      </div>
      <div className="grid" style={{ gridTemplateColumns: "2fr 1fr" }}>
        <Card title="Next week's forecast" sub="Expected calls per day with a likely range, from the last few weeks' pattern">
          <AreaLine
            id="fc"
            series={[{ values: fc.map((f) => f.high), color: colorAt(1), label: "High", dashed: true }, { values: fc.map((f) => f.expected_calls), color: colorAt(0), label: "Expected" }]}
            labels={fc.map((f) => `${f.weekday} ${f.day.slice(8)}`)}
          />
          <div className="small muted" style={{ display: "flex", gap: "1rem", flexWrap: "wrap", marginTop: 4 }}>
            {fc.map((f) => <span key={f.day}><b>{f.weekday}</b> {n1(f.expected_calls)}{f.busiest_hours.length ? ` · busy ${f.busiest_hours.map((h) => `${String(h).padStart(2, "0")}:00`).join(", ")}` : ""}</span>)}
          </div>
        </Card>
        <Card title="Staffing guide" sub={`Busiest and quietest hours · calls in the last ${d.days} days`}>
          <div className="label" style={{ marginBottom: 4 }}>Busiest</div>
          <RankedBars rows={dm.busiest_hours.map((s) => ({ label: s.label, value: s.count, sub: pct(s.share) }))} format={(v) => `${v} calls`} color={colorAt(0)} />
          <div className="label" style={{ margin: "0.8rem 0 4px" }}>Quietest (7am–8pm)</div>
          <RankedBars rows={dm.quietest_hours.map((s) => ({ label: s.label, value: s.count, sub: pct(s.share) }))} format={(v) => `${v} calls`} color={colorAt(2)} />
        </Card>
      </div>
      <div className="grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
        <Card title="Calls you'd have missed without the assistant" sub="By hour — after hours, or while your line was already busy">
          <Columns values={dm.would_have_missed_by_hour} labels={HOURS} color="linear-gradient(180deg, #f59e0b, #ef4444)" />
        </Card>
        <Card title="Average calls per day of week" sub="Per occurrence of each weekday in the window">
          <Columns tall values={dm.avg_by_weekday} labels={DAYS} />
        </Card>
      </div>
    </>
  );
}

// -- Trends -----------------------------------------------------------------------------------------

function TrendsTab({ d }: { d: InsightsReport }) {
  const t = d.trends;
  const [gran, setGran] = useState<"monthly" | "quarterly" | "yearly">("monthly");
  const pts = t[gran];
  return (
    <>
      <div className="grid kpis">
        <Metric label="This period" value={t.this_period?.calls ?? 0} sub={`calls in the last ${d.days} days`} />
        <Metric label="Same period last year" value={t.same_period_last_year?.calls ?? "—"} sub={<>calls · {signed(t.yoy_calls_pct)} year on year</>} />
        <Metric label="Busiest day" value={t.busiest_days[0] ? day(t.busiest_days[0].label) : "—"} sub={t.busiest_days[0] ? `${t.busiest_days[0].count} calls` : "no calls yet"} />
        <Metric label="Busiest month" value={t.busiest_months[0]?.label ?? "—"} sub={t.busiest_months[0] ? `${t.busiest_months[0].count} calls` : "no calls yet"} />
      </div>
      <div className="grid" style={{ gridTemplateColumns: "1fr" }}>
        <Card title="Call volume over time" sub={`Calls, answered and bookings per ${gran.replace("ly", "")} · long-range, independent of the window`}>
          <div className="chips small" style={{ marginBottom: 6 }}>
            {(["monthly", "quarterly", "yearly"] as const).map((g) => <a key={g} href="#" className={gran === g ? "active" : ""} onClick={(e) => { e.preventDefault(); setGran(g); }}>{g[0].toUpperCase() + g.slice(1)}</a>)}
          </div>
          {pts.length ? (
            <AreaLine id={`tr-${gran}`} series={[{ values: pts.map((p) => p.calls), color: colorAt(0), label: "Calls" }, { values: pts.map((p) => p.answered), color: colorAt(2), label: "Answered" }, { values: pts.map((p) => p.bookings), color: colorAt(3), label: "Bookings" }]} labels={pts.map((p) => p.label)} />
          ) : <div className="muted small">No calls in this window yet.</div>}
          <span className="legend small" style={{ marginTop: 4 }}><i style={{ background: colorAt(0) }} /> Calls <i style={{ background: colorAt(2) }} /> Answered <i style={{ background: colorAt(3) }} /> Bookings</span>
        </Card>
      </div>
      <div className="grid" style={{ gridTemplateColumns: "2fr 1fr" }}>
        <Card title="Average calls by hour of day" sub={`Per active day, averaged over the last ${d.days} days`}>
          <AreaLine id="tr-hour" series={[{ values: t.avg_by_hour, color: colorAt(1) }]} labels={Array.from({ length: 24 }, (_, h) => `${String(h).padStart(2, "0")}:00`)} />
        </Card>
        <Card title="Average calls by weekday" sub="Per occurrence of each weekday">
          <Columns tall values={t.avg_by_weekday} labels={DAYS} />
        </Card>
      </div>
      <div className="grid" style={{ gridTemplateColumns: "2fr 1fr" }}>
        <Card title="Period detail" sub="Most recent first">
          <TrendTable points={pts} unit={gran === "monthly" ? "Month" : gran === "quarterly" ? "Quarter" : "Year"} />
        </Card>
        <Card title="Seasonality" sub="Busiest months and days in the window">
          <RankedBars rows={sliceRows(t.busiest_months)} format={(v) => `${v} calls`} color={colorAt(0)} />
          <div style={{ height: "0.8rem" }} />
          <RankedBars rows={t.busiest_days.map((x) => ({ label: day(x.label), value: x.count }))} format={(v) => `${v} calls`} color={colorAt(1)} />
        </Card>
      </div>
    </>
  );
}

// -- Resolution -------------------------------------------------------------------------------------

function ResolutionTab({ d }: { d: InsightsReport }) {
  const r = d.resolution, s = d.sla, tr = d.transfers;
  return (
    <>
      <div className="grid kpis">
        <Metric label="First-contact resolution" value={pct(r.first_contact_resolution_rate)} sub="answered calls the assistant closed itself" tone={r.first_contact_resolution_rate != null && r.first_contact_resolution_rate >= 0.7 ? "good" : undefined} />
        <Metric label="Human leakage" value={pct(r.leakage_rate)} sub="answered calls that still needed a person" tone={r.leakage_rate != null && r.leakage_rate > 0.4 ? "warn" : undefined} />
        <Metric label="Time to claim" value={hrs(s.median_time_to_claim_h)} sub="median, ticket raised → claimed" />
        <Metric label="Time to first callback" value={hrs(s.median_time_to_first_callback_h)} sub="median, ticket raised → human called back" />
        <Metric label="SLA breached" value={pct(s.breach_rate)} sub={`${s.breached} of ${s.tickets} tickets`} tone={s.breached ? "bad" : "good"} />
        <Metric label="Reopened" value={pct(s.reopen_rate)} sub={`${s.reopened} tickets reopened`} tone={s.reopened ? "warn" : undefined} />
        <Metric label="AI callbacks resolved first time" value={pct(s.callback_first_attempt_rate)} sub={`${r.ai_callbacks_resolved} of ${r.ai_callbacks} AI call backs`} />
        <Metric label="Transfer answer rate" value={pct(tr.answer_rate)} sub={`${tr.answered} of ${tr.attempts} reached a person`} tone={tr.abandoned ? "warn" : undefined} />
      </div>
      <div className="grid" style={{ gridTemplateColumns: "3fr 2fr" }}>
        <Card title="Resolution funnel" sub="Where answered calls ended up">
          <Funnel steps={r.funnel} />
        </Card>
        <Card title="Resolved by the assistant, by intent" sub="Share of each intent closed without a person">
          {r.by_intent.length ? <RankedBars rows={r.by_intent.map((x) => ({ label: x.label, value: (x.share ?? 0) * 100, sub: `${Math.round(x.value ?? 0)} of ${x.count} calls` }))} format={(v) => `${Math.round(v)}%`} /> : <div className="muted small">No intents detected yet.</div>}
        </Card>
      </div>
      <div className="grid" style={{ gridTemplateColumns: "1fr 1fr 1fr" }}>
        <Card title="Ticket backlog" sub="Open tickets and oldest age per department">
          {s.backlog.length ? (
            <table className="team-table"><thead><tr><th>Department</th><th>Open</th><th>Oldest</th><th>Avg age</th></tr></thead>
              <tbody>{s.backlog.map((b) => <tr key={b.department}><td>{b.department}</td><td>{b.open}</td><td>{hrs(b.oldest_h)}</td><td>{hrs(b.avg_age_h)}</td></tr>)}</tbody></table>
          ) : <div className="muted small">No open tickets — nice.</div>}
        </Card>
        <Card title="SLA breaches by week" sub="Tickets breaching their SLA, per week raised">
          {s.breach_trend.length ? <Columns values={s.breach_trend.map((x) => x.count)} labels={s.breach_trend.map((x) => x.label)} color="linear-gradient(180deg, #f59e0b, #ef4444)" /> : <div className="muted small">No SLA breaches in this window.</div>}
        </Card>
        <Card title="Transfers by hour" sub="When callers get put through to people">
          <Columns values={tr.by_hour} labels={HOURS} />
        </Card>
      </div>
      <div className="grid" style={{ gridTemplateColumns: "1fr 1fr 1fr" }}>
        <Card title="Transfer quality by department" sub="Answer rate · abandoned while ringing · human talk time">
          <TransferTable rows={tr.by_department} />
        </Card>
        <Card title="Transfer quality by person" sub="Who picks up, and how long they spend">
          <TransferTable rows={tr.by_destination} />
        </Card>
        <Card title="Transfer vs assistant cost" sub="Estimated · human time at a staff rate vs assistant minutes">
          <Donut data={[{ name: "human", label: "Human (transfers)", count: tr.est_transfer_cost_pence }, { name: "ai", label: "Assistant", count: tr.est_ai_cost_pence }]} total="Est. cost" format={(v) => gbp(v)} empty="No transfers in this period." />
          <p className="small muted" style={{ margin: "0.6rem 0 0" }}>{tr.recorded} transferred calls recorded · {Math.round(tr.human_minutes)} human minutes · avg {secs(tr.avg_human_s)} per transfer. <Link href="/handoff">Transfers →</Link></p>
        </Card>
      </div>
    </>
  );
}

function TransferTable({ rows }: { rows: InsightsReport["transfers"]["by_department"] }) {
  if (!rows.length) return <div className="muted small">No transfers in this period.</div>;
  return (
    <table className="team-table"><thead><tr><th>Name</th><th>Attempts</th><th>Answered</th><th>Abandoned</th><th>Avg talk</th></tr></thead>
      <tbody>{rows.map((r) => <tr key={r.label}><td>{r.label}</td><td>{r.attempts}</td><td>{pct(r.answer_rate)}</td><td>{r.abandoned}</td><td>{secs(r.avg_human_s)}</td></tr>)}</tbody></table>
  );
}

// -- Team -------------------------------------------------------------------------------------------

function TeamTab({ d }: { d: InsightsReport }) {
  const w = d.workforce, c = d.cost;
  return (
    <>
      <div className="grid kpis">
        <Metric label="Handled by the assistant" value={pct(c.ai_share)} sub={`${Math.round(c.ai_minutes)} assistant min · ${Math.round(c.human_minutes)} human min`} tone="good" />
        <Metric label="Hours saved" value={n1(c.hours_saved)} sub="staff hours the assistant absorbed" tone="good" />
        <Metric label="Cost per resolved enquiry" value={c.cost_per_resolved_pence == null ? "—" : gbp(c.cost_per_resolved_pence)} sub={`est. ${gbp(c.est_ai_cost_pence)} assistant vs ${gbp(c.est_human_cost_pence)} human time`} />
        <Metric label="Unassigned open tickets" value={w.unassigned_open} sub="nobody has claimed these yet" tone={w.unassigned_open ? "warn" : "good"} />
      </div>
      <div className="grid" style={{ gridTemplateColumns: "1fr" }}>
        <Card title="Team performance" sub={`Per member · last ${d.days} days · team average ${w.team_avg_resolved == null ? "—" : n1(w.team_avg_resolved)} resolved, ${hrs(w.team_avg_resolution_h)} to resolve`}>
          {w.members.length ? (
            <table className="team-table">
              <thead><tr><th>Member</th><th>Claimed</th><th>Resolved</th><th>Callbacks</th><th>Notes</th><th>Transfers answered</th><th>Transfers missed</th><th>Avg resolution</th><th>Active days</th><th>vs team</th></tr></thead>
              <tbody>
                {w.members.map((m) => (
                  <tr key={m.label}><td>{m.label}</td><td>{m.claimed}</td><td>{m.resolved}</td><td>{m.callbacks}</td><td>{m.notes}</td><td>{m.transfers_answered}</td><td>{m.transfers_missed}</td><td>{hrs(m.avg_resolution_h)}</td><td>{m.active_days}</td>
                    <td style={{ color: m.vs_team_pct == null ? undefined : m.vs_team_pct >= 0 ? "var(--ok-fg)" : "var(--bad-fg)" }}>{signed(m.vs_team_pct)}</td></tr>
                ))}
              </tbody>
            </table>
          ) : <div className="muted small">No ticket or transfer activity by team members yet. Invite people on the <Link href="/members">Team</Link> page and route transfers to them.</div>}
        </Card>
      </div>
      <div className="grid" style={{ gridTemplateColumns: "1fr 1fr 1fr" }}>
        <Card title="Tickets resolved" sub="Per member">
          <RankedBars rows={w.members.map((m) => ({ label: m.label, value: m.resolved }))} empty="No resolved tickets yet." />
        </Card>
        <Card title="Transfers answered" sub="Per member">
          <RankedBars rows={w.members.map((m) => ({ label: m.label, value: m.transfers_answered, sub: m.transfers_missed ? `${m.transfers_missed} missed` : undefined }))} empty="No transfers answered yet." color={colorAt(2)} />
        </Card>
        <Card title="Minutes handled" sub="Assistant vs people">
          <Donut data={[{ name: "ai", label: "Assistant", count: Math.round(c.ai_minutes) }, { name: "human", label: "Team", count: Math.round(c.human_minutes) }]} total="Minutes" empty="No talk time yet." />
        </Card>
      </div>
    </>
  );
}

// -- Customers --------------------------------------------------------------------------------------

function CustomersTab({ d }: { d: InsightsReport }) {
  const cx = d.cx, it = d.intents;
  return (
    <>
      <div className="grid kpis">
        <Metric label="Quality score" value={cx.avg_qa == null ? "—" : `${cx.avg_qa.toFixed(1)} / 5`} sub={`${cx.qa_scored} calls scored · tone ${cx.avg_tone?.toFixed(1) ?? "—"} · resolution ${cx.avg_resolution?.toFixed(1) ?? "—"}`} />
        <Metric label="Frustration" value={pct(cx.frustration_rate)} sub={`${cx.frustrated} calls showed frustration`} tone={cx.frustrated ? "warn" : "good"} />
        <Metric label="Repeat calls within 7 days" value={pct(cx.repeat_rate)} sub={`${cx.repeat_within_7d} callers rang back about something — a friction signal`} tone={cx.repeat_within_7d ? "warn" : undefined} />
        <Metric label="Feedback" value={<>{cx.positive_feedback} <span className="muted" style={{ fontSize: "1rem" }}>/ {cx.negative_feedback}</span></>} sub="positive / negative flags on calls" />
      </div>
      <div className="grid" style={{ gridTemplateColumns: "2fr 1fr" }}>
        <Card title="Quality trend" sub="Average QA score per week">
          {cx.qa_trend.length ? <AreaLine id="qa" series={[{ values: cx.qa_trend.map((x) => x.value ?? 0), color: colorAt(2) }]} labels={cx.qa_trend.map((x) => x.label)} format={(v) => v.toFixed(1)} /> : <div className="muted small">QA scoring starts once calls are analysed — see <Link href="/quality">Quality</Link>.</div>}
        </Card>
        <Card title="What callers wanted" sub="Detected intents in this period">
          <Donut data={it.top.map((x) => ({ name: x.label, label: x.label, count: x.count }))} total="Calls" empty="No intents detected yet." />
        </Card>
      </div>
      <div className="grid" style={{ gridTemplateColumns: "1fr 1fr 1fr" }}>
        <Card title="Quality by intent" sub="Average score per topic">
          <RankedBars rows={cx.qa_by_intent.map((x) => ({ label: x.label, value: x.value ?? 0, sub: `${x.count} calls` }))} format={(v) => v.toFixed(1)} empty="No scored calls yet." color={colorAt(1)} />
        </Card>
        <Card title="Quality by assistant version" sub="Did the last Studio publish help?">
          <RankedBars rows={cx.qa_by_version.map((x) => ({ label: x.label, value: x.value ?? 0, sub: `${x.count} calls` }))} format={(v) => v.toFixed(1)} empty="No scored calls yet." color={colorAt(3)} />
        </Card>
        <Card title="Rising & falling topics" sub="Change vs the previous period">
          <RankedBars rows={[...it.rising, ...it.falling].map((x) => ({ label: x.label, value: x.value ?? 0, sub: `${x.count} calls`, color: (x.value ?? 0) >= 0 ? colorAt(0) : colorAt(4) }))} format={(v) => signed(v)} empty="Not enough history to compare yet." />
        </Card>
      </div>
      <div className="grid" style={{ gridTemplateColumns: "1fr" }}>
        <Card title="Questions the assistant couldn't answer" sub={`${it.unanswered_questions} unanswered questions · est. ${gbp(it.est_gap_value_pence)} at stake · add answers in Quality → Insights`}>
          {it.gaps.length ? (
            <table className="team-table"><thead><tr><th>Question</th><th>Asked</th><th>Status</th><th>Est. value</th></tr></thead>
              <tbody>{it.gaps.map((g) => <tr key={g.question}><td>{g.question}</td><td>{g.count}</td><td><span className="pill">{g.status.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase())}</span></td><td>{gbp(g.est_value_pence)}</td></tr>)}</tbody></table>
          ) : <div className="muted small">The assistant had an answer for everything it was asked.</div>}
        </Card>
      </div>
    </>
  );
}

// -- Revenue ----------------------------------------------------------------------------------------

function RevenueTab({ d }: { d: InsightsReport }) {
  const v = d.revenue, a = d.attribution;
  const cur = v.currency;
  return (
    <>
      <div className="grid kpis">
        <Metric label="Attributed value" value={gbp(v.attributed_pence, cur)} sub={`${v.leads} leads · ${v.bookings} bookings · avg job ${gbp(v.avg_job_value_pence, cur)}`} tone="good" />
        <Metric label="Value missed" value={gbp(v.missed_pence, cur)} sub="calls that went unanswered" tone={v.missed_pence ? "bad" : undefined} />
        <Metric label="Value unresolved" value={gbp(v.unresolved_pence, cur)} sub="leads sitting in open tickets" tone={v.unresolved_pence ? "warn" : undefined} />
        <Metric label="Lead → booking" value={hrs(v.median_lead_to_booking_h)} sub={`median · ${pct(v.booking_rate)} of leads book`} />
        <Metric label="Lead rate" value={pct(v.lead_rate)} sub={`${v.leads} of ${v.calls} calls were leads`} />
        <Metric label="Repeat callers" value={pct(v.repeat_caller_share)} sub="callers who rang more than once" />
      </div>
      <div className="grid" style={{ gridTemplateColumns: "2fr 1fr" }}>
        <Card title="Lead conversion by hour" sub="Share of calls that were leads, per hour of day">
          <AreaLine id="lead-h" series={[{ values: v.by_hour.map((x) => x * 100), color: colorAt(3) }]} labels={Array.from({ length: 24 }, (_, h) => `${String(h).padStart(2, "0")}:00`)} format={(x) => `${Math.round(x)}%`} />
        </Card>
        <Card title="Value by intent" sub="Estimated value of leads per topic">
          <Donut data={v.by_intent.map((x) => ({ name: x.label, label: x.label, count: Math.round(x.value ?? 0) }))} total="Est. value" format={(p) => gbp(p, cur)} empty="No leads attributed yet — set your job values on the Value page." />
        </Card>
      </div>
      <div className="grid" style={{ gridTemplateColumns: "1fr 1fr 1fr" }}>
        <Card title="Marketing attribution" sub={`Calls per tracking-number channel · ${a.untracked_calls} untracked`}>
          <Donut data={a.channels.map((x) => ({ name: x.label, label: x.label, count: x.count }))} total="Calls" empty="Add tracking numbers on the Value page to attribute calls to channels." />
        </Card>
        <Card title="Leads & bookings by channel" sub="Which channels turn into work">
          {a.leads_by_channel.length ? (
            <table className="team-table"><thead><tr><th>Channel</th><th>Calls</th><th>Leads</th><th>Bookings</th><th>Est. value</th></tr></thead>
              <tbody>{a.channels.map((c) => {
                const l = a.leads_by_channel.find((x) => x.label === c.label), b = a.bookings_by_channel.find((x) => x.label === c.label);
                return <tr key={c.label}><td>{c.label}</td><td>{c.count}</td><td>{l?.count ?? 0}</td><td>{b?.count ?? 0}</td><td>{gbp(c.value ?? 0, cur)}</td></tr>;
              })}</tbody></table>
          ) : <div className="muted small">No tracked channels yet.</div>}
        </Card>
        <Breakdown title="Leads by source" data={v.by_source.map((x) => ({ name: x.label, count: x.count, label: x.label }))} empty="No leads in this period." />
      </div>
      <div className="grid" style={{ gridTemplateColumns: "1fr" }}>
        <Card title="Top customers by value" sub="Contacts with the most attributed value in this period">
          <RankedBars rows={v.top_customers.map((x) => ({ label: x.label, value: x.value ?? 0, sub: `${x.count} calls` }))} format={(p) => gbp(p, cur)} empty="No attributed value yet." />
        </Card>
      </div>
    </>
  );
}
