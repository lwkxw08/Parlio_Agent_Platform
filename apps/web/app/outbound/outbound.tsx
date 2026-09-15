"use client";

import Link from "next/link";
import { useState } from "react";
import {
  API_URL,
  type Assistant,
  type Jurisdiction,
  type Lead,
  type OutboundCall,
  type OutboundPolicy,
  type OutboundPurpose,
  type OutboundSummary,
  type Suppression,
  del,
  fetchLeads,
  fetchOutboundCalls,
  fetchOutboundSummary,
  fetchSuppressions,
  post,
  put,
} from "@/lib/api";

type Tab = "leads" | "queue" | "policy" | "suppressions" | "intake";

const PURPOSES: [OutboundPurpose, string, string][] = [
  ["lead_followup", "Speed-to-lead", "Call new web/webhook leads within the target time, qualify, book or ticket."],
  ["ticket_callback", "Ticket callbacks", "Ring callers back when a ticket is marked for AI callback."],
  ["reminder", "Appointment reminders", "Remind before a booking (hours below)."],
  ["confirmation", "Booking confirmations", "Confirm a new booking by phone."],
  ["no_show", "No-show follow-up", "Call to rebook after a missed appointment."],
  ["review_request", "Review requests", "Ask for a review after a completed appointment."],
];
const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
const STATUS_CLS: Record<string, string> = {
  completed: "ok", in_progress: "ok", dialing: "warn", retry: "warn", scheduled: "", exhausted: "bad", failed: "bad", suppressed: "bad", cancelled: "",
};
const LEAD_CLS: Record<string, string> = {
  booked: "ok", qualified: "ok", contacted: "ok", ticketed: "warn", calling: "warn", new: "", lost: "bad", opted_out: "bad", unreachable: "bad",
};
const fmt = (iso: string | null) => (iso ? new Date(iso).toLocaleString("en-GB", { dateStyle: "short", timeStyle: "short" }) : "—");
const secs = (s: number | null) => (s == null ? "—" : s < 90 ? `${Math.round(s)}s` : `${Math.round(s / 60)} min`);
const pct = (v: number | null) => (v == null ? "—" : `${Math.round(v * 100)}%`);

type Props = {
  tenant: string; canManage: boolean; summary: OutboundSummary | null; policy: OutboundPolicy; jurisdictions: Record<string, Jurisdiction>;
  leads: Lead[]; calls: OutboundCall[]; suppressions: Suppression[]; assistants: Assistant[];
};

export default function Outbound(p: Props) {
  const [tab, setTab] = useState<Tab>("leads");
  const [summary, setSummary] = useState(p.summary);
  const [leads, setLeads] = useState(p.leads);
  const [calls, setCalls] = useState(p.calls);
  const [sups, setSups] = useState(p.suppressions);
  const [policy, setPolicy] = useState(p.policy);
  const [msg, setMsg] = useState<string | null>(null);
  const q = `?tenant_id=${p.tenant}`;

  const refresh = async () => {
    const [s, l, c, su] = await Promise.all([fetchOutboundSummary(p.tenant), fetchLeads(p.tenant), fetchOutboundCalls(p.tenant), fetchSuppressions(p.tenant)]);
    if (s) setSummary(s);
    if (l) setLeads(l);
    if (c) setCalls(c);
    if (su) setSups(su);
  };
  const flash = (m: string) => { setMsg(m); setTimeout(() => setMsg(null), 4000); };

  const cancel = async (id: string) => { if (await post(`/v1/outbound/calls/${id}/cancel${q}`)) { flash("Cancelled"); await refresh(); } };
  const dialNow = async (id: string) => {
    const r = await post<OutboundCall>(`/v1/outbound/calls/${id}/dial-now${q}`);
    flash(r ? `Dialling ${r.to}` : "Refused (window, cap or suppression)");
    await refresh();
  };
  const suppress = async (phone: string, reason = "opt_out") => {
    if (await post(`/v1/outbound/suppressions${q}`, { phone, reason })) { flash(`${phone} will not be called`); await refresh(); }
  };
  const unsuppress = async (phone: string) => { if (await del(`/v1/outbound/suppressions${q}&phone=${encodeURIComponent(phone)}`)) await refresh(); };

  const kpi = (label: string, value: string | number, sub?: string) => (
    <div className="card"><div className="label">{label}</div><div className="value">{value}</div>{sub && <div className="small muted">{sub}</div>}</div>
  );

  return (
    <>
      {!policy.enabled && <p className="muted"><span className="pill warn">Outbound off</span> No calls are placed until enabled in Policy.</p>}
      <div className="grid kpis">
        {kpi("Leads", summary?.leads ?? 0, `${summary?.leads_by_status.new ?? 0} new · ${summary?.leads_by_status.booked ?? 0} booked`)}
        {kpi("Speed to lead", secs(summary?.speed_to_lead_median_s ?? null), `${pct(summary?.speed_to_lead_within_target ?? null)} within ${policy.speed_to_lead_target_s}s target`)}
        {kpi("Contact rate", pct(summary?.contact_rate ?? null), `${summary?.jobs ?? 0} calls · ${summary?.queued ?? 0} queued`)}
        {kpi("Booked / ticketed", `${summary?.by_outcome.booked ?? 0} / ${summary?.by_outcome.ticketed ?? 0}`, `${summary?.by_outcome.opt_out ?? 0} opt-outs`)}
        {kpi("Do-not-call", summary?.suppressed ?? sups.length)}
      </div>
      <div className="tabs">
        {(["leads", "queue", "policy", "suppressions", "intake"] as Tab[]).map((t) => (
          <button key={t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>
            {t === "leads" ? "Leads" : t === "queue" ? "Call queue & history" : t === "policy" ? "Policy & compliance" : t === "suppressions" ? "Do-not-call list" : "Lead intake"}
          </button>
        ))}
      </div>
      {msg && <p className="muted small">{msg}</p>}

      {tab === "leads" && <Leads tenant={p.tenant} leads={leads} calls={calls} canManage={p.canManage} onChange={refresh} onSuppress={suppress} />}
      {tab === "queue" && <Queue tenant={p.tenant} calls={calls} assistants={p.assistants} canManage={p.canManage} policy={policy} onCancel={cancel} onDial={dialNow} onChange={refresh} />}
      {tab === "policy" && <Policy tenant={p.tenant} policy={policy} jurisdictions={p.jurisdictions} canManage={p.canManage} onSaved={(pol) => { setPolicy(pol); flash("Policy saved"); }} />}
      {tab === "suppressions" && <Suppressions sups={sups} canManage={p.canManage} onAdd={suppress} onRemove={unsuppress} />}
      {tab === "intake" && <Intake tenant={p.tenant} policy={policy} />}
    </>
  );
}

// -- Leads ---------------------------------------------------------------------------------------

function Leads({ tenant, leads, calls, canManage, onChange, onSuppress }: {
  tenant: string; leads: Lead[]; calls: OutboundCall[]; canManage: boolean; onChange: () => Promise<void>; onSuppress: (phone: string) => Promise<void>;
}) {
  const [form, setForm] = useState({ name: "", phone: "", email: "", interest: "", notes: "", consent: true, call_now: true });
  const [busy, setBusy] = useState(false);
  const lastCall = (l: Lead) => calls.find((c) => c.lead_id === l.id) ?? null;
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    await post(`/v1/outbound/leads?tenant_id=${tenant}`, { ...form, email: form.email || null, interest: form.interest || null, notes: form.notes || null });
    setForm({ name: "", phone: "", email: "", interest: "", notes: "", consent: true, call_now: true });
    await onChange();
    setBusy(false);
  };
  return (
    <>
      {canManage && (
        <form className="section form" onSubmit={submit}>
          <h2>Add a lead</h2>
          <p className="hint">Leads also arrive automatically from your web form, Zapier/Make webhook or the API (see Lead intake). With “call now” the assistant rings them within the speed-to-lead target, inside calling hours.</p>
          <div className="two">
            <label>Name<input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
            <label>Phone (E.164)<input required placeholder="+447700900123" value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} /></label>
            <label>Email<input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></label>
            <label>Interested in<input placeholder="e.g. boiler service" value={form.interest} onChange={(e) => setForm({ ...form, interest: e.target.value })} /></label>
          </div>
          <label>Notes<textarea value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} /></label>
          <div className="row">
            <label className="check small"><input type="checkbox" checked={form.consent} onChange={(e) => setForm({ ...form, consent: e.target.checked })} /> They asked to be called back (consent)</label>
            <label className="check small"><input type="checkbox" checked={form.call_now} onChange={(e) => setForm({ ...form, call_now: e.target.checked })} /> Call now</label>
            <button type="submit" disabled={busy || !form.phone}>Add lead</button>
          </div>
        </form>
      )}
      <table>
        <thead><tr><th>Received</th><th>Lead</th><th>Source</th><th>Status</th><th>Speed to lead</th><th>Last call</th><th /></tr></thead>
        <tbody>
          {leads.map((l) => {
            const c = lastCall(l);
            return (
              <tr key={l.id}>
                <td className="small">{fmt(l.created_at)}</td>
                <td>{l.name || "—"}<div className="small muted">{l.phone}{l.interest ? ` · ${l.interest}` : ""}</div></td>
                <td><span className="pill">{l.source.replace("_", " ")}</span>{!l.consent && <span className="pill warn" title="No consent recorded">no consent</span>}</td>
                <td><span className={`pill ${LEAD_CLS[l.status] ?? ""}`}>{l.status.replace("_", " ")}</span></td>
                <td>{secs(l.speed_to_lead_s)}</td>
                <td className="small">{c ? <>{c.status.replace("_", " ")}{c.outcome ? ` · ${c.outcome.replace("_", " ")}` : ""}{c.attempts.length ? ` · ${c.attempts.length}/${c.max_attempts} attempts` : ""}</> : "—"}</td>
                <td className="row">{canManage && l.status !== "opted_out" && <button className="small" onClick={() => onSuppress(l.phone)}>Do not call</button>}</td>
              </tr>
            );
          })}
          {!leads.length && <tr><td colSpan={7} className="muted">No leads yet. Connect your web form or webhook under Lead intake.</td></tr>}
        </tbody>
      </table>
    </>
  );
}

// -- Queue ---------------------------------------------------------------------------------------

function Queue({ tenant, calls, assistants, canManage, policy, onCancel, onDial, onChange }: {
  tenant: string; calls: OutboundCall[]; assistants: Assistant[]; canManage: boolean; policy: OutboundPolicy;
  onCancel: (id: string) => Promise<void>; onDial: (id: string) => Promise<void>; onChange: () => Promise<void>;
}) {
  const [form, setForm] = useState({ purpose: "ticket_callback" as OutboundPurpose, to: "", name: "", when: "", assistant_id: assistants[0]?.assistant_id ?? "" });
  const [filter, setFilter] = useState<"active" | "all">("active");
  const active = new Set(["scheduled", "dialing", "in_progress", "retry"]);
  const shown = calls.filter((c) => filter === "all" || active.has(c.status)).sort((a, b) => b.scheduled_at.localeCompare(a.scheduled_at));
  const schedule = async (e: React.FormEvent) => {
    e.preventDefault();
    await post(`/v1/outbound/calls?tenant_id=${tenant}`, {
      purpose: form.purpose, to: form.to, name: form.name || null, assistant_id: form.assistant_id || null,
      when: form.when ? new Date(form.when).toISOString() : null,
    });
    setForm({ ...form, to: "", name: "", when: "" });
    await onChange();
  };
  return (
    <>
      {canManage && (
        <form className="section form" onSubmit={schedule}>
          <h2>Schedule a call</h2>
          <p className="hint">Calls are placed only inside the calling window ({policy.window_start.slice(0, 5)}–{policy.window_end.slice(0, 5)} {policy.timezone}, {policy.days.join("/")}) and never to numbers on the do-not-call list.</p>
          <div className="two">
            <label>Purpose
              <select value={form.purpose} onChange={(e) => setForm({ ...form, purpose: e.target.value as OutboundPurpose })}>
                {PURPOSES.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
              </select>
            </label>
            <label>Phone (E.164)<input required placeholder="+447700900123" value={form.to} onChange={(e) => setForm({ ...form, to: e.target.value })} /></label>
            <label>Name<input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
            <label>When (blank = next available)<input type="datetime-local" value={form.when} onChange={(e) => setForm({ ...form, when: e.target.value })} /></label>
            {assistants.length > 1 && (
              <label>Assistant
                <select value={form.assistant_id} onChange={(e) => setForm({ ...form, assistant_id: e.target.value })}>
                  {assistants.map((a) => <option key={a.assistant_id} value={a.assistant_id}>{a.name}</option>)}
                </select>
              </label>
            )}
          </div>
          <div className="row"><button type="submit" disabled={!form.to}>Schedule</button></div>
        </form>
      )}
      <div className="tabs">
        <button className={filter === "active" ? "active" : ""} onClick={() => setFilter("active")}>Queued</button>
        <button className={filter === "all" ? "active" : ""} onClick={() => setFilter("all")}>All history</button>
      </div>
      <table>
        <thead><tr><th>Scheduled</th><th>To</th><th>Purpose</th><th>Status</th><th>Attempts</th><th>Outcome</th><th /></tr></thead>
        <tbody>
          {shown.map((c) => (
            <tr key={c.id}>
              <td className="small">{fmt(c.scheduled_at)}</td>
              <td>{c.name || "—"}<div className="small muted">{c.to}</div></td>
              <td><span className="pill">{PURPOSES.find(([id]) => id === c.purpose)?.[1] ?? c.purpose}</span></td>
              <td><span className={`pill ${STATUS_CLS[c.status] ?? ""}`}>{c.status.replace("_", " ")}</span>{c.reason && <div className="small muted">{c.reason}</div>}</td>
              <td className="small">{c.attempts.length}/{c.max_attempts}{c.attempts.length ? <div className="muted">{c.attempts.map((a) => a.outcome.replace("_", " ")).join(", ")}</div> : null}</td>
              <td className="small">
                {c.outcome ? c.outcome.replace("_", " ") : "—"}{c.outcome_detail && <div className="muted">{c.outcome_detail}</div>}
                {c.call_id && <div><Link href={`/calls/${c.call_id}`}>Transcript &amp; recording</Link></div>}
                {c.ticket_id && <div><Link href={`/tickets/${c.ticket_id}`}>Ticket</Link></div>}
              </td>
              <td className="row">
                {canManage && active.has(c.status) && c.status !== "in_progress" && <button className="small" onClick={() => onDial(c.id)} title="Call straight away, even outside calling hours or past the daily cap (do-not-call still applies)">Dial now</button>}
                {canManage && active.has(c.status) && <button className="small" onClick={() => onCancel(c.id)}>Cancel</button>}
              </td>
            </tr>
          ))}
          {!shown.length && <tr><td colSpan={7} className="muted">{filter === "active" ? "Nothing queued." : "No outbound calls yet."}</td></tr>}
        </tbody>
      </table>
    </>
  );
}

// -- Policy --------------------------------------------------------------------------------------

function Policy({ tenant, policy, jurisdictions, canManage, onSaved }: {
  tenant: string; policy: OutboundPolicy; jurisdictions: Record<string, Jurisdiction>; canManage: boolean; onSaved: (p: OutboundPolicy) => void;
}) {
  const [f, setF] = useState(policy);
  const [err, setErr] = useState<string | null>(null);
  const j = jurisdictions[f.jurisdiction];
  const applyJurisdiction = (code: string) => {
    const jj = jurisdictions[code];
    setF(jj ? { ...f, jurisdiction: code, timezone: jj.timezone, window_start: jj.start, window_end: jj.end, days: jj.days, max_attempts: jj.max_attempts } : { ...f, jurisdiction: code });
  };
  const togglePurpose = (id: OutboundPurpose) => setF({ ...f, purposes: f.purposes.includes(id) ? f.purposes.filter((x) => x !== id) : [...f.purposes, id] });
  const toggleDay = (d: string) => setF({ ...f, days: f.days.includes(d) ? f.days.filter((x) => x !== d) : DAYS.filter((x) => x === d || f.days.includes(x)) });
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    const r = await put<OutboundPolicy>(`/v1/outbound/policy?tenant_id=${tenant}`, { ...f, caller_id: f.caller_id || null });
    if (r.ok) { setErr(null); onSaved(r.data); } else setErr(r.error);
  };
  const num = (k: keyof OutboundPolicy) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: Number(e.target.value) });
  return (
    <form className="section form" onSubmit={save}>
      <div className="row between">
        <h2>Calling policy</h2>
        <label className="check"><input type="checkbox" checked={f.enabled} disabled={!canManage} onChange={(e) => setF({ ...f, enabled: e.target.checked })} /> Outbound calling enabled</label>
      </div>
      <p className="hint">Only solicited calls are placed: leads who asked to be contacted, callers who requested a callback, and customers with a booking. Every number is checked against the do-not-call list, the calling window and daily caps before each attempt.</p>
      <h3 className="small">What the assistant may call about</h3>
      <div className="grid">
        {PURPOSES.map(([id, label, blurb]) => (
          <label key={id} className="check small"><input type="checkbox" checked={f.purposes.includes(id)} disabled={!canManage} onChange={() => togglePurpose(id)} /> <b>{label}</b><span className="small muted">{blurb}</span></label>
        ))}
      </div>
      <h3 className="small">Hours &amp; jurisdiction</h3>
      <div className="two">
        <label>Jurisdiction preset
          <select value={f.jurisdiction} disabled={!canManage} onChange={(e) => applyJurisdiction(e.target.value)}>
            {Object.entries(jurisdictions).map(([code, jj]) => <option key={code} value={code}>{code} — {jj.note}</option>)}
          </select>
          {j && <span className="small muted">Preset: {j.start}–{j.end} {j.timezone}, {j.days.join("/")}, max {j.max_attempts} attempts</span>}
        </label>
        <label>Time zone<input value={f.timezone} disabled={!canManage} onChange={(e) => setF({ ...f, timezone: e.target.value })} /></label>
        <label>Calling window from<input type="time" value={f.window_start.slice(0, 5)} disabled={!canManage} onChange={(e) => setF({ ...f, window_start: e.target.value })} /></label>
        <label>to<input type="time" value={f.window_end.slice(0, 5)} disabled={!canManage} onChange={(e) => setF({ ...f, window_end: e.target.value })} /></label>
      </div>
      <div className="row">
        {DAYS.map((d) => <label key={d} className="check small"><input type="checkbox" checked={f.days.includes(d)} disabled={!canManage} onChange={() => toggleDay(d)} /> {d}</label>)}
      </div>
      <h3 className="small">Attempts &amp; pacing</h3>
      <div className="two">
        <label>Max attempts per call<input type="number" min={1} max={5} value={f.max_attempts} disabled={!canManage} onChange={num("max_attempts")} /></label>
        <label>Gap between attempts (min)<input type="number" min={15} value={f.retry_gap_min} disabled={!canManage} onChange={num("retry_gap_min")} /></label>
        <label>Daily cap per number<input type="number" min={1} max={5} value={f.daily_cap_per_number} disabled={!canManage} onChange={num("daily_cap_per_number")} /></label>
        <label>Speed-to-lead target (seconds)<input type="number" min={10} value={f.speed_to_lead_target_s} disabled={!canManage} onChange={num("speed_to_lead_target_s")} /></label>
        <label>Reminder (hours before booking)<input type="number" min={1} value={f.reminder_hours_before} disabled={!canManage} onChange={num("reminder_hours_before")} /></label>
        <label>Review request (hours after)<input type="number" min={1} value={f.review_request_delay_h} disabled={!canManage} onChange={num("review_request_delay_h")} /></label>
        <label>Caller ID shown (E.164)<input placeholder="Your Parlio number" value={f.caller_id ?? ""} disabled={!canManage} onChange={(e) => setF({ ...f, caller_id: e.target.value })} /></label>
        <label>Opt-out phrases<input value={f.opt_out_keywords.join(", ")} disabled={!canManage} onChange={(e) => setF({ ...f, opt_out_keywords: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })} /></label>
      </div>
      <div className="row">
        <label className="check small"><input type="checkbox" checked={f.require_consent} disabled={!canManage} onChange={(e) => setF({ ...f, require_consent: e.target.checked })} /> Only call leads with recorded consent</label>
        <label className="check small"><input type="checkbox" checked={f.leave_voicemail} disabled={!canManage} onChange={(e) => setF({ ...f, leave_voicemail: e.target.checked })} /> Leave a voicemail when unanswered</label>
        <label className="check small" title="Off: callback tickets wait for your team, who can choose “Send to AI call back” on the ticket. On: every callback request is queued for the assistant automatically.">
          <input type="checkbox" checked={f.auto_ticket_callbacks} disabled={!canManage} onChange={(e) => setF({ ...f, auto_ticket_callbacks: e.target.checked })} /> Automatically hand callback requests to the assistant
        </label>
      </div>
      {err && <p className="small" style={{ color: "var(--bad-fg)" }}>{err}</p>}
      {canManage && <div className="row"><button type="submit">Save policy</button></div>}
    </form>
  );
}

// -- Suppressions --------------------------------------------------------------------------------

function Suppressions({ sups, canManage, onAdd, onRemove }: {
  sups: Suppression[]; canManage: boolean; onAdd: (phone: string, reason: string) => Promise<void>; onRemove: (phone: string) => Promise<void>;
}) {
  const [phone, setPhone] = useState("");
  const [reason, setReason] = useState("opt_out");
  return (
    <>
      {canManage && (
        <form className="section form" onSubmit={async (e) => { e.preventDefault(); await onAdd(phone, reason); setPhone(""); }}>
          <h2>Do-not-call list</h2>
          <p className="hint">Numbers here are never dialled. Callers who say “stop calling me” during a call are added automatically; TPS/complaint entries can be added manually.</p>
          <div className="row">
            <input required placeholder="+447700900123" value={phone} onChange={(e) => setPhone(e.target.value)} />
            <select value={reason} onChange={(e) => setReason(e.target.value)}>
              {["opt_out", "tps", "complaint", "wrong_number", "blocked"].map((r) => <option key={r} value={r}>{r.replace("_", " ")}</option>)}
            </select>
            <button type="submit" disabled={!phone}>Add</button>
          </div>
        </form>
      )}
      <table>
        <thead><tr><th>Number</th><th>Reason</th><th>Source</th><th>Added</th><th /></tr></thead>
        <tbody>
          {sups.map((s) => (
            <tr key={s.id}>
              <td>{s.phone}</td><td><span className="pill">{s.reason.replace("_", " ")}</span></td><td className="small">{s.source}</td><td className="small">{fmt(s.created_at)}</td>
              <td>{canManage && <button className="small" onClick={() => { if (confirm(`Allow calls to ${s.phone} again?`)) onRemove(s.phone); }}>Remove</button>}</td>
            </tr>
          ))}
          {!sups.length && <tr><td colSpan={5} className="muted">Empty.</td></tr>}
        </tbody>
      </table>
    </>
  );
}

// -- Intake --------------------------------------------------------------------------------------

function Intake({ tenant, policy }: { tenant: string; policy: OutboundPolicy }) {
  const url = `${API_URL}/v1/public/leads/${tenant}`;
  const sample = JSON.stringify({ token: policy.form_token, name: "Sam Jones", phone: "+447700900123", email: "sam@example.com", interest: "boiler service", consent: true, consent_text: "Please call me back about my enquiry" }, null, 2);
  const html = `<form action="${url}" method="post" data-parlio>
  <input name="name" placeholder="Name">
  <input name="phone" placeholder="Mobile" required>
  <input name="interest" placeholder="What can we help with?">
  <label><input type="checkbox" name="consent" required> Please call me back</label>
  <input type="hidden" name="token" value="${policy.form_token}">
  <button>Request a call</button>
</form>
<script>
document.querySelector('[data-parlio]').addEventListener('submit', async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  const body = Object.fromEntries(f); body.consent = f.get('consent') === 'on';
  await fetch('${url}', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
  e.target.innerHTML = '<p>Thanks — we will call you shortly.</p>';
});
</script>`;
  return (
    <>
      <div className="section">
        <h2>Web form (any website)</h2>
        <p className="hint">Paste this into your site. Submissions become leads and, with consent, the assistant calls within {policy.speed_to_lead_target_s}s during calling hours. The token identifies your organisation; rotate it by saving a new one via the API if it leaks.</p>
        <pre className="small" style={{ whiteSpace: "pre-wrap", overflowX: "auto" }}>{html}</pre>
      </div>
      <div className="section">
        <h2>Zapier / Make / webhooks</h2>
        <p className="hint">POST JSON to <code>{url}</code> with the token:</p>
        <pre className="small" style={{ whiteSpace: "pre-wrap" }}>{sample}</pre>
        <p className="hint">Or, with an inbound API key from Integrations → Connected apps, POST the same body (no token) to <code>{API_URL}/v1/inbound/leads</code> with header <code>X-Api-Key</code>. Add <code>&quot;call_now&quot;: false</code> to store the lead without dialling.</p>
      </div>
    </>
  );
}
