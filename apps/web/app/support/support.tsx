"use client";

import Link from "next/link";
import { useState } from "react";
import {
  type KbArticle,
  type SupportPriority,
  type SupportTicket,
  fetchKb,
  fetchSupportTickets,
  openSupportTicket,
  rateSupportTicket,
  replySupportTicket,
  when,
} from "@/lib/api";
import { humanize } from "@/app/breakdown";
import { openHelp } from "@/app/help";

export const PRIORITY: Record<SupportPriority, string> = { p1: "P1 — service down", p2: "P2 — major fault", p3: "P3 — question / minor", p4: "P4 — feature request" };
export const prCls = (p: SupportPriority) => (p === "p1" ? "bad" : p === "p2" ? "warn" : "");
export const stCls = (s: SupportTicket["status"]) => (s === "resolved" || s === "closed" ? "ok" : s.startsWith("waiting") ? "warn" : "accent");
const OPEN = new Set(["open", "in_progress", "waiting_customer", "waiting_provider"]);

export function TicketEvents({ t }: { t: SupportTicket }) {
  return (
    <div className="small" style={{ display: "grid", gap: "0.4rem" }}>
      {t.events.map((e, i) => (
        <div key={i} className="row" style={{ alignItems: "flex-start", opacity: e.public ? 1 : 0.7 }}>
          <span className="muted" style={{ minWidth: 130 }}>{when(e.at)}</span>
          <span className={`pill ${e.type === "note" ? "warn" : ""}`}>{humanize(e.type)}</span>
          <span><strong>{e.by}</strong> {e.text}</span>
        </div>
      ))}
    </div>
  );
}

export default function Support({ tenant, tickets: initial, kb: kbInitial }: { tenant: string; tickets: SupportTicket[]; kb: KbArticle[] }) {
  const [tickets, setTickets] = useState(initial);
  const [sel, setSel] = useState<string | null>(initial.find((t) => OPEN.has(t.status))?.id ?? null);
  const [form, setForm] = useState({ subject: "", body: "", priority: "p3" as SupportPriority });
  const [reply, setReply] = useState("");
  const [q, setQ] = useState("");
  const [kb, setKb] = useState(kbInitial);
  const [msg, setMsg] = useState<string | null>(null);
  const reload = async () => { const r = await fetchSupportTickets(tenant); if (r) setTickets(r); };
  const cur = tickets.find((t) => t.id === sel) ?? null;

  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    const r = await openSupportTicket(tenant, form);
    if (!r.ok) return setMsg(r.error);
    setForm({ subject: "", body: "", priority: "p3" });
    setSel(r.data.id);
    setMsg(`Ticket ${r.data.id} opened — target first response ${r.data.sla_due_at ? when(r.data.sla_due_at) : "soon"}`);
    await reload();
  };
  const send = async () => {
    if (!cur || !reply.trim()) return;
    const r = await replySupportTicket(tenant, cur.id, reply);
    if (!r.ok) return setMsg(r.error);
    setReply("");
    await reload();
  };
  const rate = async (score: number) => {
    if (!cur) return;
    const r = await rateSupportTicket(tenant, cur.id, score);
    if (!r.ok) return setMsg(r.error);
    setMsg("Thanks for your feedback");
    await reload();
  };
  const search = async (v: string) => { setQ(v); const r = await fetchKb(tenant, v); if (r) setKb(r); };

  return (
    <>
      {msg && <p className="small" style={{ color: "var(--accent)" }}>{msg}</p>}
      <div className="grid" style={{ gridTemplateColumns: "1fr 2fr" }}>
        <div>
          <form className="section form" onSubmit={create}>
            <h2>Open a ticket</h2>
            <p className="hint">Our AI support agent answers common questions instantly and runs diagnostics (test call, forwarding and SIP checks) before a person picks it up. Check <Link href={`/health?tenant=${tenant}`}>Health</Link> first for live status.</p>
            <label>Subject<input required maxLength={200} value={form.subject} onChange={(e) => setForm({ ...form, subject: e.target.value })} /></label>
            <label>Details<textarea rows={4} value={form.body} onChange={(e) => setForm({ ...form, body: e.target.value })} placeholder="What happened, when, and the caller's number if relevant" /></label>
            <label>Priority
              <select value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value as SupportPriority })}>
                {(Object.keys(PRIORITY) as SupportPriority[]).map((p) => <option key={p} value={p}>{PRIORITY[p]}</option>)}
              </select>
            </label>
            <button className="primary" type="submit">Submit</button>
          </form>
          <div className="section form">
            <h2>Help articles</h2>
            <p className="small muted" style={{ marginTop: 0 }}>
              Need a how-to? <button type="button" className="link" onClick={() => openHelp()}>Ask Parlio</button> answers questions from the full user guide and takes you to the exact setting.
            </p>
            <input placeholder="Search e.g. forwarding, SIP, billing" value={q} onChange={(e) => search(e.target.value)} style={{ width: "100%", marginBottom: "0.6rem" }} />
            {kb.map((a) => (
              <details key={a.id} style={{ marginBottom: "0.4rem" }}>
                <summary>{a.title}</summary>
                <p className="small">{a.body}</p>
                {a.url && <Link href={a.url} className="small">Open</Link>}
              </details>
            ))}
            {kb.length === 0 && <p className="muted small">No matches.</p>}
          </div>
        </div>
        <div>
          <div className="section">
            <h2>Your tickets</h2>
            {tickets.length === 0 ? <p className="muted small">No tickets yet.</p> : (
              <table>
                <thead><tr><th>Ticket</th><th>Priority</th><th>Status</th><th>Updated</th></tr></thead>
                <tbody>
                  {tickets.map((t) => (
                    <tr key={t.id} onClick={() => setSel(t.id)} style={{ cursor: "pointer", background: t.id === sel ? "var(--hover)" : undefined }}>
                      <td><strong>{t.subject}</strong><div className="small muted">{t.id}</div></td>
                      <td><span className={`pill ${prCls(t.priority)}`}>{t.priority.toUpperCase()}</span></td>
                      <td><span className={`pill ${stCls(t.status)}`}>{t.status.replaceAll("_", " ")}</span>{t.sla_breached && <span className="pill bad" style={{ marginLeft: 4 }}>SLA</span>}</td>
                      <td className="small">{when(t.updated_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
          {cur && (
            <div className="section">
              <div className="row" style={{ justifyContent: "space-between" }}>
                <h2>{cur.subject}</h2>
                <span className="small muted">{cur.sla_due_at && OPEN.has(cur.status) ? `first response due ${when(cur.sla_due_at)}` : cur.resolved_at ? `resolved ${when(cur.resolved_at)}` : ""}</span>
              </div>
              {cur.fault && <p className="small"><strong>Diagnosis:</strong> {cur.fault.headline} — {cur.fault.explanation}</p>}
              <TicketEvents t={cur} />
              {OPEN.has(cur.status) ? (
                <div className="form" style={{ marginTop: "0.8rem" }}>
                  <textarea rows={3} value={reply} onChange={(e) => setReply(e.target.value)} placeholder="Reply to support" style={{ width: "100%" }} />
                  <div className="row" style={{ marginTop: "0.4rem" }}><button className="primary" onClick={send} disabled={!reply.trim()}>Send</button></div>
                </div>
              ) : cur.csat_score == null ? (
                <div className="row" style={{ marginTop: "0.8rem" }}>
                  <span className="small">How did we do?</span>
                  {[1, 2, 3, 4, 5].map((n) => <button key={n} className="ghost" onClick={() => rate(n)}>{n}</button>)}
                </div>
              ) : <p className="small muted" style={{ marginTop: "0.8rem" }}>You rated this {cur.csat_score}/5 — thank you.</p>}
            </div>
          )}
        </div>
      </div>
    </>
  );
}
