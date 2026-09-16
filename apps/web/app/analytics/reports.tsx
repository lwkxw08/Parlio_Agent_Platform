"use client";

import Link from "next/link";
import { useEffect, useState, useTransition } from "react";
import { type ReportOverview, type ReportSchedule, downloadInsightsCsv, fetchReports, saveReportSchedule, sendReportNow, when } from "@/lib/api";

const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
export const SECTION_LABEL: Record<string, string> = {
  summary: "Summary", demand: "Demand & staffing", forecast: "Next-week forecast", resolution: "Resolution funnel", sla: "Tickets & callback SLA",
  transfers: "Transfer quality", intents: "Top intents", gaps: "Unanswered questions", revenue: "Revenue & pipeline", cx: "Customer experience",
  workforce: "Team", attribution: "Marketing attribution", cost: "Cost & efficiency", trends: "Trends",
};

export default function Reports({ days, timezone }: { days: number; timezone: string }) {
  const [data, setData] = useState<ReportOverview | null>(null);
  const [s, setS] = useState<ReportSchedule | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, start] = useTransition();
  const [expanded, setExpanded] = useState<string | null>(null);

  const load = async () => {
    const r = await fetchReports();
    if (r.ok) { setData(r.data); setS(r.data.schedule); } else setMsg(r.error);
  };
  useEffect(() => { void load(); }, []);

  const flash = (m: string) => { setMsg(m); setTimeout(() => setMsg(null), 3000); };
  const save = (e: React.FormEvent) => {
    e.preventDefault();
    if (!s) return;
    start(async () => {
      const r = await saveReportSchedule(undefined, s);
      if (r.ok) { setS(r.data); flash("Schedule saved"); } else flash(r.error);
    });
  };
  const sendNow = () => start(async () => {
    const r = await sendReportNow(undefined, days);
    if (r.ok) { flash("Report sent to your notification channels"); await load(); } else flash(r.error);
  });
  const toggle = (sec: string) => s && setS({ ...s, sections: s.sections.includes(sec) ? s.sections.filter((x) => x !== sec) : [...s.sections, sec] });

  if (!data || !s) return <div className="card muted small">{msg ?? "Loading…"}</div>;
  const sections = data.sections.filter((x) => x !== "summary");
  return (
    <div className="grid" style={{ gridTemplateColumns: "3fr 2fr" }}>
      <div className="card">
        <h2>Scheduled report<span className="sub">Emailed / posted via the “Scheduled analytics report” event under Integrations → Notifications</span></h2>
        <form className="form" onSubmit={save}>
          <label style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
            <input type="checkbox" checked={s.enabled} onChange={(e) => setS({ ...s, enabled: e.target.checked })} /> Send automatically
          </label>
          <div className="two">
            <label>Cadence
              <select value={s.cadence} onChange={(e) => setS({ ...s, cadence: e.target.value as ReportSchedule["cadence"] })}>
                <option value="weekly">Weekly</option><option value="monthly">Monthly</option>
              </select>
            </label>
            {s.cadence === "weekly" ? (
              <label>Day
                <select value={s.weekday} onChange={(e) => setS({ ...s, weekday: Number(e.target.value) })}>
                  {WEEKDAYS.map((d, i) => <option key={d} value={i}>{d}</option>)}
                </select>
              </label>
            ) : (
              <label>Day of month
                <input type="number" min={1} max={28} value={s.day_of_month} onChange={(e) => setS({ ...s, day_of_month: Number(e.target.value) })} />
              </label>
            )}
            <label>Hour (UK time)
              <input type="number" min={0} max={23} value={s.hour} onChange={(e) => setS({ ...s, hour: Number(e.target.value) })} />
            </label>
          </div>
          <div>
            <div className="label" style={{ marginBottom: 6 }}>Sections to include</div>
            <div className="chips">
              {sections.map((sec) => (
                <button key={sec} type="button" className={s.sections.includes(sec) ? "active" : ""} onClick={() => toggle(sec)}>{SECTION_LABEL[sec] ?? sec}</button>
              ))}
            </div>
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <button className="primary" disabled={busy}>Save schedule</button>
            <button type="button" className="ghost" disabled={busy} onClick={sendNow}>Send now (last {days} days)</button>
            {msg && <span className="muted small">{msg}</span>}
          </div>
          <p className="muted small" style={{ margin: 0 }}>Nobody subscribed yet? <Link href="/integrations">Add a notification rule</Link> for “Scheduled analytics report”.</p>
        </form>
      </div>
      <div className="card">
        <h2>Export CSV<span className="sub">Last {days} days · {timezone}</span></h2>
        <div className="chips" style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
          {data.sections.map((sec) => (
            <button key={sec} type="button" onClick={async () => { const err = await downloadInsightsCsv(sec, { days, timezone }); if (err) flash(err); }}>{SECTION_LABEL[sec] ?? sec}</button>
          ))}
        </div>
        <h2 style={{ marginTop: "1.2rem" }}>Sent reports</h2>
        {!data.history.length && <div className="muted small">No reports sent yet.</div>}
        <ul style={{ paddingLeft: "1.1rem", margin: 0 }}>
          {data.history.map((h) => (
            <li key={h.id} style={{ marginBottom: 6 }}>
              <a href="#" onClick={(e) => { e.preventDefault(); setExpanded(expanded === h.id ? null : h.id); }}>{h.title}</a>
              <span className="muted small" style={{ marginLeft: 6 }}>{when(h.sent_at)}</span>
              {expanded === h.id && <pre className="small" style={{ whiteSpace: "pre-wrap", marginTop: 6 }}>{h.body}</pre>}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
