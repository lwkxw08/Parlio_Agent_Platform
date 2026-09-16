"use client";

import Link from "next/link";
import { useEffect, useState, useTransition } from "react";
import { Metric } from "@/app/charts";
import {
  type AdvisorOverview,
  type AdvisorSettings,
  type Recommendation,
  adviceAction,
  fetchAdvisor,
  runAdvisor,
  saveAdvisorSettings,
  when,
} from "@/lib/api";

const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const PRIORITY: Record<number, { label: string; cls: string }> = {
  1: { label: "High", cls: "bad" },
  2: { label: "Medium", cls: "warn" },
  3: { label: "Low", cls: "" },
};
const AREA: Record<string, string> = {
  demand: "Demand", transfers: "Transfers", sla: "Tickets & callbacks", revenue: "Revenue", faq: "Knowledge",
  quality: "Quality", cx: "Customer experience", workforce: "Team", marketing: "Marketing",
};
const ACTION_HINT: Record<string, string> = {
  rule: "Adds a business rule to your assistant (a new version is published).",
  faq: "Adds an FAQ answer to your assistant — you write the answer.",
  hours: "Open Studio → Business hours to extend cover.",
  staffing: "A change for your rota — marked as applied so we can measure it.",
  outbound: "Open Outbound to enable AI call backs.",
  tracking: "Open Value → Tracking numbers.",
  coaching: "A conversation with the team member — marked as applied so we can measure it.",
  note: "Marked as applied so the next report shows whether the metric moved.",
};
const LINKS: Record<string, string> = { hours: "/assistant", outbound: "/outbound", tracking: "/value", faq: "/assistant", rule: "/assistant" };

const conf = (c: number) => (c >= 0.75 ? "High confidence" : c >= 0.5 ? "Medium confidence" : "Early signal");
const outcomeText = (r: Recommendation) => {
  const o = r.outcome;
  if (!o) return r.status === "applied" ? "Measuring — outcome after the next full period." : null;
  const dir = o.improved == null ? "unchanged" : o.improved ? "improved" : "not improved yet";
  return `${o.metric.replace(/_/g, " ").replace(/\./g, " → ")} ${dir}: ${fmt(o.before)} → ${fmt(o.after)}${o.delta_pct == null ? "" : ` (${o.delta_pct > 0 ? "+" : ""}${o.delta_pct.toFixed(0)}%)`}`;
};
const fmt = (v: number) => (Math.abs(v) <= 1 && !Number.isInteger(v) ? `${Math.round(v * 100)}%` : Number.isInteger(v) ? String(v) : v.toFixed(1));

function RecCard({ rec, onChange }: { rec: Recommendation; onChange: (r: Recommendation) => void }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [actionIdx, setActionIdx] = useState(0);
  const [err, setErr] = useState<string | null>(null);
  const [busy, start] = useTransition();
  const action = rec.actions[actionIdx];
  const needsText = action && (action.kind === "faq" || action.question);
  const p = PRIORITY[rec.priority] ?? PRIORITY[3];
  const closed = rec.status !== "new";

  const act = (kind: "apply" | "dismiss" | "snooze", body: Record<string, unknown> = {}) =>
    start(async () => {
      const r = await adviceAction(rec.id, kind, body);
      if (r.ok) { onChange(r.data); setOpen(false); setErr(null); } else setErr(r.error);
    });

  return (
    <div className={`card rec${closed ? " rec-closed" : ""}`}>
      <div className="rec-head">
        <div>
          <div className="small muted" style={{ marginBottom: 2 }}>
            <span className={`pill ${p.cls}`}>{p.label}</span>
            <span style={{ marginLeft: 6 }}>{AREA[rec.area] ?? rec.area}</span>
            <span style={{ marginLeft: 6 }}>· {conf(rec.confidence)}</span>
            {rec.wording_source === "llm" && <span style={{ marginLeft: 6 }}>· AI-worded</span>}
            {closed && <span className="pill" style={{ marginLeft: 6 }}>{rec.status === "snoozed" && rec.snoozed_until ? `Snoozed until ${when(rec.snoozed_until)}` : rec.status[0].toUpperCase() + rec.status.slice(1)}</span>}
          </div>
          <h3 style={{ margin: "0 0 4px" }}>{rec.title}</h3>
          <p style={{ margin: 0 }}>{rec.summary}</p>
        </div>
      </div>
      <div className="rec-evidence">
        {rec.evidence.map((e) => (
          <div key={e.label} className="rec-ev"><span className="muted small">{e.label}</span><strong>{e.value}</strong></div>
        ))}
      </div>
      <p className="small" style={{ margin: "0.5rem 0 0" }}><strong>Expected impact:</strong> {rec.expected_impact}</p>
      {outcomeText(rec) && <p className="small muted" style={{ margin: "0.3rem 0 0" }}>{outcomeText(rec)}</p>}
      {!closed && (
        <div className="rec-actions">
          {!open && <button className="primary" onClick={() => setOpen(true)} disabled={!rec.actions.length}>Apply…</button>}
          <button className="ghost" onClick={() => act("snooze", { days: 14 })} disabled={busy}>Snooze 2 weeks</button>
          <button className="ghost" onClick={() => act("dismiss")} disabled={busy}>Dismiss</button>
          {err && <span className="small" style={{ color: "var(--bad)" }}>{err}</span>}
        </div>
      )}
      {open && !closed && (
        <div className="form rec-apply">
          {rec.actions.length > 1 && (
            <label>What will you do?
              <select value={actionIdx} onChange={(e) => { setActionIdx(Number(e.target.value)); setText(""); }}>
                {rec.actions.map((a, i) => <option key={i} value={i}>{a.label}</option>)}
              </select>
            </label>
          )}
          {action && <p className="small muted" style={{ margin: 0 }}>{ACTION_HINT[action.kind] ?? action.label} {LINKS[action.kind] && <Link href={LINKS[action.kind]}>Open</Link>}</p>}
          {needsText && (
            <label>{action.kind === "faq" ? `Answer to: “${action.question}”` : "Details"}
              <textarea value={text} onChange={(e) => setText(e.target.value)} placeholder={action.kind === "faq" ? "Type the answer the assistant should give" : ""} />
            </label>
          )}
          <div>
            <button className="primary" disabled={busy || (action?.kind === "faq" && !text.trim())} onClick={() => act("apply", { action_index: actionIdx, text: text.trim() || null })}>Confirm</button>
            <button className="ghost" style={{ marginLeft: 6 }} onClick={() => setOpen(false)}>Cancel</button>
          </div>
        </div>
      )}
    </div>
  );
}

function SettingsForm({ initial, onSaved }: { initial: AdvisorSettings; onSaved: (s: AdvisorSettings) => void }) {
  const [s, setS] = useState(initial);
  const [msg, setMsg] = useState<string | null>(null);
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    const r = await saveAdvisorSettings(undefined, s);
    setMsg(r.ok ? "Saved" : r.error);
    if (r.ok) onSaved(r.data);
    setTimeout(() => setMsg(null), 2500);
  };
  return (
    <form className="form" onSubmit={save}>
      <label style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
        <input type="checkbox" checked={s.enabled} onChange={(e) => setS({ ...s, enabled: e.target.checked })} /> Run the advisor weekly
      </label>
      <label style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
        <input type="checkbox" checked={s.weekly_digest} onChange={(e) => setS({ ...s, weekly_digest: e.target.checked })} /> Send a weekly digest (to whoever is subscribed to “Weekly advisor recommendations” under Integrations → Notifications)
      </label>
      <div className="two">
        <label>Digest day
          <select value={s.digest_weekday} onChange={(e) => setS({ ...s, digest_weekday: Number(e.target.value) })}>
            {WEEKDAYS.map((d, i) => <option key={d} value={i}>{d}</option>)}
          </select>
        </label>
        <label>Digest hour (UK time)
          <input type="number" min={0} max={23} value={s.digest_hour} onChange={(e) => setS({ ...s, digest_hour: Number(e.target.value) })} />
        </label>
        <label>Look back (days)
          <input type="number" min={7} max={365} value={s.lookback_days} onChange={(e) => setS({ ...s, lookback_days: Number(e.target.value) })} />
        </label>
      </div>
      <label style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
        <input type="checkbox" checked={s.use_llm_wording} onChange={(e) => setS({ ...s, use_llm_wording: e.target.checked })} /> Let AI phrase recommendations in plain English (only your aggregate numbers are shared — never names or phone numbers)
      </label>
      <div><button className="primary">Save</button> {msg && <span className="muted small" style={{ marginLeft: 8 }}>{msg}</span>}</div>
    </form>
  );
}

export default function Advisor() {
  const [data, setData] = useState<AdvisorOverview | null>(null);
  const [error, setError] = useState<{ status: number; text: string } | null>(null);
  const [showClosed, setShowClosed] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [running, start] = useTransition();

  const load = async () => {
    const r = await fetchAdvisor();
    if (r.ok) { setData(r.data); setError(null); } else setError({ status: r.status, text: r.error });
  };
  useEffect(() => { void load(); }, []);

  const run = () => start(async () => {
    const r = await runAdvisor();
    if (!r.ok) setError({ status: r.status, text: r.error });
    await load();
  });
  const replace = (rec: Recommendation) => setData((d) => d && { ...d, recommendations: d.recommendations.map((x) => (x.id === rec.id ? rec : x)) });

  if (error?.status === 403) {
    return (
      <div className="card" style={{ textAlign: "center", padding: "2rem" }}>
        <h3 style={{ marginTop: 0 }}>AI business advisor</h3>
        <p className="muted">Weekly, evidence-backed recommendations on staffing, transfers, knowledge gaps and revenue — with one-click fixes and outcome tracking.</p>
        <p className="muted small">Available on Growth and above.</p>
        <p><Link href="/billing">See plans →</Link></p>
      </div>
    );
  }
  if (!data) return <div className="card muted small">{error ? error.text : "Loading…"}</div>;

  const open = data.recommendations.filter((r) => r.status === "new");
  const closed = data.recommendations.filter((r) => r.status !== "new");
  const applied = closed.filter((r) => r.status === "applied");
  const improved = applied.filter((r) => r.outcome?.improved).length;

  return (
    <>
      <div className="grid kpis">
        <Metric label="Open recommendations" value={open.length} sub={open.filter((r) => r.priority === 1).length ? `${open.filter((r) => r.priority === 1).length} high priority` : "nothing urgent"} tone={open.some((r) => r.priority === 1) ? "warn" : undefined} />
        <Metric label="Applied" value={applied.length} sub={applied.length ? `${improved} improved so far` : "apply one to start measuring"} tone={improved ? "good" : undefined} />
        <Metric label="Last analysis" value={data.last_run ? when(data.last_run.created_at) : "—"} sub={data.last_run ? `${data.last_run.trigger} · ${data.last_run.generated} new` : "not run yet"} />
        <Metric label="Next digest" value={data.settings.weekly_digest && data.settings.enabled ? WEEKDAYS[data.settings.digest_weekday] : "Off"} sub={data.settings.weekly_digest && data.settings.enabled ? `${String(data.settings.digest_hour).padStart(2, "0")}:00 UK time` : "weekly digest disabled"} />
      </div>
      <div className="an-head" style={{ marginTop: "0.25rem" }}>
        <p className="muted small" style={{ margin: 0 }}>Every recommendation is built from your own call data — the numbers shown are the evidence. Apply the ones you act on so the next report shows whether they worked.</p>
        <span className="chips">
          <button onClick={run} disabled={running}>{running ? "Analysing…" : "Analyse now"}</button>
          <button className={showClosed ? "active" : ""} onClick={() => setShowClosed((v) => !v)}>History ({closed.length})</button>
          <button className={showSettings ? "active" : ""} onClick={() => setShowSettings((v) => !v)}>Settings</button>
        </span>
      </div>
      {error && <div className="card small" style={{ color: "var(--bad)" }}>{error.text}</div>}
      {showSettings && (
        <div className="card">
          <h2>Advisor settings</h2>
          <SettingsForm initial={data.settings} onSaved={(s) => setData({ ...data, settings: s })} />
        </div>
      )}
      <div className="rec-list">
        {open.map((r) => <RecCard key={r.id} rec={r} onChange={replace} />)}
        {!open.length && (
          <div className="card muted small" style={{ textAlign: "center", padding: "1.5rem" }}>
            {data.last_run ? "Nothing needs your attention right now — the advisor found no significant patterns in this period." : "No analysis yet. Click “Analyse now” for your first set of recommendations."}
          </div>
        )}
        {showClosed && closed.map((r) => <RecCard key={r.id} rec={r} onChange={replace} />)}
      </div>
    </>
  );
}
