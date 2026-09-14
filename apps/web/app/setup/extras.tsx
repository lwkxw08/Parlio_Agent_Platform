"use client";

import Link from "next/link";
import { useState } from "react";
import {
  type FirstWeekReport, type WhiteGloveArea, type WhiteGloveEligibility,
  cancelWhiteGlove, requestWhiteGlove, when,
} from "@/lib/api";

const STATUS_LABEL: Record<string, string> = {
  requested: "Requested — we'll confirm a slot within one working day",
  scheduled: "Scheduled",
  in_progress: "In progress",
  completed: "Completed",
  cancelled: "Cancelled",
};

export function FirstWeekCard({ report, tenant }: { report: FirstWeekReport; tenant: string }) {
  const money = (p: number) => `£${(p / 100).toLocaleString("en-GB", { maximumFractionDigits: 0 })}`;
  return (
    <div className="section">
      <h2>Your first {report.days_live > 0 && report.days_live < 7 ? `${report.days_live} day${report.days_live === 1 ? "" : "s"}` : "week"}</h2>
      <div className="grid">
        <div className="card"><div className="label">Calls answered</div><div className="value">{report.answered}</div><div className="small muted">{report.after_hours} out of hours · {report.minutes} min</div></div>
        <div className="card"><div className="label">Bookings</div><div className="value">{report.bookings}</div><div className="small muted">{report.qualified_leads} qualified lead{report.qualified_leads === 1 ? "" : "s"}</div></div>
        <div className="card"><div className="label">Value attributed</div><div className="value">{money(report.attributed_pence)}</div><div className="small muted"><Link href={`/value?tenant=${tenant}`}>See how</Link></div></div>
      </div>
      <ul className="small" style={{ marginTop: ".75rem" }}>
        {report.highlights.map((h) => <li key={h}>{h}</li>)}
      </ul>
      {report.next_steps.length > 0 && (
        <p className="hint"><strong>Suggested next:</strong> {report.next_steps.join(" · ")}</p>
      )}
      {report.top_intents.length > 0 && (
        <p className="small muted">Most common reasons for calling: {report.top_intents.map(([k, n]) => `${k} (${n})`).join(", ")}</p>
      )}
    </div>
  );
}

export function WhiteGloveCard({ tenant, initial, areas, canManage }: {
  tenant: string; initial: WhiteGloveEligibility; areas: Record<string, string>; canManage: boolean;
}) {
  const [state, setState] = useState(initial);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [form, setForm] = useState({ contact_name: "", contact_email: "", contact_phone: "", notes: "", slots: "" });
  const [picked, setPicked] = useState<WhiteGloveArea[]>(["config_review", "test_calls"]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true); setErr(null);
    const r = await requestWhiteGlove(tenant, {
      contact_name: form.contact_name, contact_email: form.contact_email, contact_phone: form.contact_phone || null,
      areas: picked, notes: form.notes,
      preferred_slots: form.slots.split("\n").map((s) => s.trim()).filter(Boolean).slice(0, 5),
    });
    setBusy(false);
    if (!r.ok) return setErr(r.error);
    setState({ ...state, open_request: r.data });
    setOpen(false);
  };

  const req = state.open_request;
  return (
    <div className="section">
      <h2>White-glove onboarding</h2>
      {!state.eligible ? (
        <p className="hint">{state.reason} — <Link href={`/billing?tenant=${tenant}&tab=plan`}>compare plans</Link>. Everyone can still reach us via <Link href={`/support?tenant=${tenant}`}>Support</Link>.</p>
      ) : req ? (
        <div className="card">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <strong>{STATUS_LABEL[req.status]}</strong>
            <span className={`pill ${req.status === "completed" ? "ok" : req.status === "requested" ? "warn" : ""}`}>{req.status.replace("_", " ")}</span>
          </div>
          <div className="small muted">Requested {when(req.created_at)}{req.scheduled_at ? ` · session ${when(req.scheduled_at)}` : ""}{req.assigned_to ? ` · with ${req.assigned_to}` : ""}</div>
          <div className="small" style={{ marginTop: ".5rem" }}>{req.areas.map((a) => areas[a] ?? a).join(" · ")}</div>
          {req.staff_notes.length > 0 && (
            <ul className="small" style={{ marginTop: ".5rem" }}>{req.staff_notes.map((n, i) => <li key={i}><span className="muted">{when(n.at)} — {n.author}:</span> {n.text}</li>)}</ul>
          )}
          {canManage && req.status !== "completed" && (
            <button className="ghost small" style={{ marginTop: ".5rem" }} disabled={busy} onClick={async () => {
              setBusy(true); const r = await cancelWhiteGlove(tenant, req.id); setBusy(false);
              if (r.ok) setState({ ...state, open_request: null });
            }}>Cancel request</button>
          )}
        </div>
      ) : (
        <>
          <p className="hint">Included in your plan: a Parlio specialist reviews your assistant, helps connect forwarding or your PBX, runs test calls with you and trains your team — over a video call.</p>
          {!open ? (
            <button className="primary" disabled={!canManage} onClick={() => setOpen(true)}>Book a session</button>
          ) : (
            <form className="form" onSubmit={submit}>
              <div className="two">
                <label>Your name<input required value={form.contact_name} onChange={(e) => setForm({ ...form, contact_name: e.target.value })} /></label>
                <label>Email<input required type="email" value={form.contact_email} onChange={(e) => setForm({ ...form, contact_email: e.target.value })} /></label>
                <label>Phone (optional)<input value={form.contact_phone} onChange={(e) => setForm({ ...form, contact_phone: e.target.value })} /></label>
                <label>Preferred times (one per line)<textarea rows={3} placeholder={"Tue 10:00–12:00\nThu afternoon"} value={form.slots} onChange={(e) => setForm({ ...form, slots: e.target.value })} /></label>
              </div>
              <fieldset>
                <legend>What would you like help with?</legend>
                <div className="two">
                  {Object.entries(areas).map(([k, label]) => (
                    <label key={k} className="check">
                      <input type="checkbox" checked={picked.includes(k as WhiteGloveArea)} onChange={(e) => setPicked(e.target.checked ? [...picked, k as WhiteGloveArea] : picked.filter((x) => x !== k))} /> {label}
                    </label>
                  ))}
                </div>
              </fieldset>
              <label>Anything else we should know?<textarea rows={3} value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} /></label>
              {err && <p className="small" style={{ color: "var(--bad-fg)" }}>{err}</p>}
              <div className="row">
                <button className="primary" disabled={busy || picked.length === 0}>{busy ? "Sending…" : "Request session"}</button>
                <button type="button" className="ghost" onClick={() => setOpen(false)}>Cancel</button>
              </div>
            </form>
          )}
        </>
      )}
    </div>
  );
}
