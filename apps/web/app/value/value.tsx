"use client";

import Link from "next/link";
import { useState } from "react";
import {
  type DigestRecord,
  type TrackingNumber,
  type ValueOverview,
  type ValueSettings,
  del,
  fetchValue,
  money,
  post,
  put,
  when,
} from "@/lib/api";

const TABS = [["report", "Revenue & leads"], ["channels", "Tracking numbers"], ["digest", "Weekly digest"], ["assumptions", "Assumptions"]] as const;
type Tab = (typeof TABS)[number][0];
const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const GRADE: Record<string, string> = { hot: "bad", warm: "warn", cold: "" };

export default function Value({ tenant, days, canManage, overview }: { tenant: string; days: number; canManage: boolean; overview: ValueOverview }) {
  const [tab, setTab] = useState<Tab>("report");
  const [ov, setOv] = useState(overview);
  const [msg, setMsg] = useState<string | null>(null);
  const flash = (m: string) => { setMsg(m); setTimeout(() => setMsg(null), 5000); };
  const refresh = async () => { const r = await fetchValue(tenant, days); if (r) setOv(r); };
  const cur = ov.report.currency;
  const q = `?tenant_id=${tenant}`;
  return (
    <>
      <div className="tabs">
        {TABS.map(([id, label]) => <button key={id} className={tab === id ? "active" : ""} onClick={() => setTab(id)}>{label}</button>)}
      </div>
      {msg && <p className="small" style={{ color: "var(--accent)" }}>{msg}</p>}
      {tab === "report" && <Report tenant={tenant} days={days} ov={ov} />}
      {tab === "channels" && <Channels tenant={tenant} canManage={canManage} ov={ov} cur={cur} refresh={refresh} flash={flash} />}
      {tab === "digest" && (
        <Digest canManage={canManage} digests={ov.digests} settings={ov.settings} send={async () => {
          const r = await post<DigestRecord>(`/v1/value/digest/send${q}`);
          flash(r ? `Digest sent: ${r.title}` : "Send failed");
          await refresh();
        }} />
      )}
      {tab === "assumptions" && <Assumptions tenant={tenant} canManage={canManage} settings={ov.settings} onSaved={async (s) => { setOv({ ...ov, settings: s }); await refresh(); flash("Saved — report recalculated."); }} flash={flash} />}
    </>
  );
}

function Report({ tenant, days, ov }: { tenant: string; days: number; ov: ValueOverview }) {
  const r = ov.report;
  const cur = r.currency;
  const kpi = (label: string, value: string | number, sub?: string, cls = "") => (
    <div className={`card ${cls}`}><div className="label">{label}</div><div className="value">{value}</div>{sub && <div className="small muted">{sub}</div>}</div>
  );
  const intents = Object.entries(r.intents).sort((a, b) => b[1] - a[1]);
  const max = intents[0]?.[1] ?? 1;
  return (
    <>
      <div className="chips" style={{ marginBottom: "0.8rem" }}>
        {[7, 30, 90].map((d) => <Link key={d} href={`/value?tenant=${tenant}&days=${d}`} className={d === days ? "active" : ""}>Last {d} days</Link>)}
      </div>
      <div className="grid">
        {kpi("Attributed revenue", money(r.attributed_pence, cur), `${r.bookings} booking${r.bookings === 1 ? "" : "s"} × job value`)}
        {kpi("Pipeline", money(r.pipeline_pence, cur), `${r.qualified_leads} qualified lead${r.qualified_leads === 1 ? "" : "s"} × close rate`)}
        {kpi("Recovered by AI", money(r.recovered_by_ai_pence, cur), "calls answered outside hours or while busy")}
        {kpi("Missed revenue", money(r.missed_revenue_pence, cur), `${r.missed} missed call${r.missed === 1 ? "" : "s"}`)}
        {kpi("Calls", r.calls, `${r.answered} answered`)}
        {kpi("Hot leads", r.hot_leads, "score ≥ 70")}
      </div>
      <p className="hint" style={{ marginTop: "0.6rem" }}>
        Estimates use your <em>Assumptions</em> (average job value, lead-to-sale rate, missed-call lead rate) applied to real call outcomes and bookings —
        tune them to match your business. Nothing here is billed or shared with callers.
      </p>
      <div className="two-col">
        <div className="section">
          <h2>What callers wanted</h2>
          {intents.length === 0 ? <p className="muted small">No calls in this period.</p> : (
            <div className="hbars">
              {intents.map(([k, v]) => (
                <div key={k} className="row"><span style={{ width: 110 }}>{k}</span><div className="bars" style={{ flex: 1 }}><div style={{ width: `${(v / max) * 100}%` }} /></div><span className="small muted">{v}</span></div>
              ))}
            </div>
          )}
        </div>
        <div className="section">
          <h2>Top leads</h2>
          {r.top_leads.length === 0 ? <p className="muted small">No scored leads yet.</p> : (
            <table>
              <thead><tr><th>Call</th><th>Score</th><th>Intent</th><th>Why</th></tr></thead>
              <tbody>
                {r.top_leads.map((l) => (
                  <tr key={l.call_id}>
                    <td><Link href={`/calls/${l.call_id}`}>{l.call_id.slice(0, 10)}</Link></td>
                    <td><span className={`pill ${GRADE[l.grade] ?? ""}`}>{l.grade} · {l.score}</span></td>
                    <td>{l.intent ?? "—"}</td>
                    <td className="small muted">{l.reasons.join("; ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
      <div className="section">
        <h2>By marketing channel</h2>
        {r.channels.length === 0 ? <p className="muted small">Add tracking numbers to attribute calls to channels.</p> : (
          <table>
            <thead><tr><th>Channel</th><th>Calls</th><th>Leads</th><th>Bookings</th><th>Attributed</th><th>Spend (period)</th><th>Cost / lead</th></tr></thead>
            <tbody>
              {r.channels.map((c) => (
                <tr key={c.channel}>
                  <td>{c.channel}</td><td>{c.calls}</td><td>{c.leads}</td><td>{c.bookings}</td>
                  <td>{money(c.attributed_pence, cur)}</td><td>{c.cost_pence ? money(c.cost_pence, cur) : "—"}</td>
                  <td>{c.cost_per_lead_pence != null ? money(c.cost_per_lead_pence, cur) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}

function Channels({ tenant, canManage, ov, cur, refresh, flash }: { tenant: string; canManage: boolean; ov: ValueOverview; cur: string; refresh: () => Promise<void>; flash: (m: string) => void }) {
  const [form, setForm] = useState({ e164: "", channel: "", campaign: "", monthly_cost_pence: 0 });
  const q = `?tenant_id=${tenant}`;
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    const r = await put<TrackingNumber>(`/v1/value/tracking-numbers${q}`, { ...form, campaign: form.campaign || null });
    if (!r.ok) return flash(`Could not save: ${r.error}`);
    setForm({ e164: "", channel: "", campaign: "", monthly_cost_pence: 0 }); flash("Tracking number saved."); await refresh();
  };
  const remove = async (id: string) => { if (await del(`/v1/value/tracking-numbers/${id}${q}`)) await refresh(); };
  return (
    <div className="two-col">
      <div className="section">
        <h2>Tracking numbers</h2>
        <p className="hint">Point a different number at the assistant per channel (Google Ads, website, van, leaflets…). Calls to each number are attributed to that channel so you can see cost per lead. Numbers are provisioned on the <Link href={`/telephony?tenant=${tenant}`}>Telephony</Link> page.</p>
        {ov.tracking_numbers.length === 0 ? <p className="muted small">None yet.</p> : (
          <table>
            <thead><tr><th>Number</th><th>Channel</th><th>Campaign</th><th>Monthly spend</th><th></th></tr></thead>
            <tbody>
              {ov.tracking_numbers.map((t) => (
                <tr key={t.id}>
                  <td>{t.e164}</td><td>{t.channel}</td><td>{t.campaign ?? "—"}</td><td>{t.monthly_cost_pence ? money(t.monthly_cost_pence, cur) : "—"}</td>
                  <td>{canManage && <button className="small" onClick={() => remove(t.id)}>Remove</button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {canManage && (
        <form className="section form" onSubmit={save}>
          <h2>Add a tracking number</h2>
          <div className="row">
            <label>Number (E.164)<input value={form.e164} required pattern="^\+\d{7,15}$" onChange={(e) => setForm({ ...form, e164: e.target.value.replace(/\s+/g, "") })} placeholder="+442046206823" /></label>
            <label>Channel<input value={form.channel} required onChange={(e) => setForm({ ...form, channel: e.target.value })} placeholder="Google Ads" /></label>
          </div>
          <div className="row">
            <label>Campaign (optional)<input value={form.campaign} onChange={(e) => setForm({ ...form, campaign: e.target.value })} placeholder="boiler-service-spring" /></label>
            <label>Monthly spend ({cur})<input type="number" min={0} step={1} value={form.monthly_cost_pence / 100} onChange={(e) => setForm({ ...form, monthly_cost_pence: Math.round(Number(e.target.value) * 100) })} /></label>
          </div>
          <button className="primary" type="submit">Save</button>
        </form>
      )}
    </div>
  );
}

function Digest({ canManage, digests, settings, send }: { canManage: boolean; digests: DigestRecord[]; settings: ValueSettings; send: () => Promise<void> }) {
  const [open, setOpen] = useState<DigestRecord | null>(digests[0] ?? null);
  return (
    <div className="two-col">
      <div className="section">
        <h2>Weekly owner digest</h2>
        <p className="hint">
          {settings.digest_enabled ? `Sent every ${DAYS[settings.digest_weekday]} at ${String(settings.digest_hour).padStart(2, "0")}:00` : "Disabled"} via your
          notification channels (event <code>owner.digest</code>: email, SMS or Slack). Change the schedule under <em>Assumptions</em>.
        </p>
        {canManage && <button className="primary" onClick={send}>Send digest now</button>}
        <h2 style={{ marginTop: "1rem" }}>History</h2>
        {digests.length === 0 ? <p className="muted small">No digests sent yet.</p> : (
          <table>
            <thead><tr><th>Sent</th><th>Title</th></tr></thead>
            <tbody>{digests.map((d) => <tr key={d.id} onClick={() => setOpen(d)} style={{ cursor: "pointer" }}><td className="small">{when(d.sent_at)}</td><td>{d.title}</td></tr>)}</tbody>
          </table>
        )}
      </div>
      <div className="section">
        <h2>{open ? open.title : "Preview"}</h2>
        {open ? <pre className="small" style={{ whiteSpace: "pre-wrap" }}>{open.body}</pre> : <p className="muted small">Select a digest to read it.</p>}
      </div>
    </div>
  );
}

function Assumptions({ tenant, canManage, settings, onSaved, flash }: { tenant: string; canManage: boolean; settings: ValueSettings; onSaved: (s: ValueSettings) => Promise<void>; flash: (m: string) => void }) {
  const [s, setS] = useState(settings);
  const q = `?tenant_id=${tenant}`;
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    const r = await put<ValueSettings>(`/v1/value/settings${q}`, {
      currency: s.currency, avg_job_value_pence: s.avg_job_value_pence, booking_value_pence: s.booking_value_pence, lead_to_sale_rate: s.lead_to_sale_rate,
      missed_call_lead_rate: s.missed_call_lead_rate, digest_enabled: s.digest_enabled, digest_weekday: s.digest_weekday, digest_hour: s.digest_hour,
    });
    if (!r.ok) return flash(`Could not save: ${r.error}`);
    await onSaved(r.data);
  };
  const pounds = (p: number | null) => (p == null ? "" : String(p / 100));
  return (
    <form className="section form" onSubmit={save}>
      <h2>Business assumptions</h2>
      <p className="hint">Used only to turn call outcomes into money figures on this page and in the digest.</p>
      <div className="row">
        <label>Average job value ({s.currency})<input type="number" min={0} value={pounds(s.avg_job_value_pence)} disabled={!canManage} onChange={(e) => setS({ ...s, avg_job_value_pence: Math.round(Number(e.target.value) * 100) })} /></label>
        <label>Value of a booking, if different<input type="number" min={0} value={pounds(s.booking_value_pence)} disabled={!canManage} onChange={(e) => setS({ ...s, booking_value_pence: e.target.value === "" ? null : Math.round(Number(e.target.value) * 100) })} placeholder="same as job value" /></label>
      </div>
      <div className="row">
        <label>Qualified leads that become a sale (%)<input type="number" min={0} max={100} value={Math.round(s.lead_to_sale_rate * 100)} disabled={!canManage} onChange={(e) => setS({ ...s, lead_to_sale_rate: Number(e.target.value) / 100 })} /></label>
        <label>Missed calls that were a lead (%)<input type="number" min={0} max={100} value={Math.round(s.missed_call_lead_rate * 100)} disabled={!canManage} onChange={(e) => setS({ ...s, missed_call_lead_rate: Number(e.target.value) / 100 })} /></label>
      </div>
      <h2 style={{ marginTop: "1rem" }}>Weekly digest</h2>
      <label className="check"><input type="checkbox" checked={s.digest_enabled} disabled={!canManage} onChange={(e) => setS({ ...s, digest_enabled: e.target.checked })} /> Send a weekly summary to owners</label>
      <div className="row">
        <label>Day<select value={s.digest_weekday} disabled={!canManage} onChange={(e) => setS({ ...s, digest_weekday: Number(e.target.value) })}>{DAYS.map((d, i) => <option key={d} value={i}>{d}</option>)}</select></label>
        <label>Hour (UK time)<input type="number" min={0} max={23} value={s.digest_hour} disabled={!canManage} onChange={(e) => setS({ ...s, digest_hour: Number(e.target.value) })} /></label>
      </div>
      {canManage && <button className="primary" type="submit">Save</button>}
    </form>
  );
}
