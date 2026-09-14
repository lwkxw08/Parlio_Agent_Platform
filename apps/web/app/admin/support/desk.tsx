"use client";

import Link from "next/link";
import { useState } from "react";
import {
  type SupportPriority,
  type SupportStatus,
  type SupportTicket,
  type TagReview,
  fetchDeskTickets,
  fetchTagReview,
  request,
  when,
} from "@/lib/api";
import { Stat } from "../ui";
import { PRIORITY, TicketEvents, prCls, stCls } from "../../support/support";

const STATUSES: SupportStatus[] = ["open", "in_progress", "waiting_customer", "waiting_provider", "resolved", "closed"];
const OPEN = new Set<SupportStatus>(["open", "in_progress", "waiting_customer", "waiting_provider"]);

type Props = { tickets: SupportTicket[]; review: TagReview | null; canAct: boolean; tenantFilter: string | null; staffName: string };

export default function Desk({ tickets: initial, review: initialReview, canAct, tenantFilter, staffName }: Props) {
  const [tickets, setTickets] = useState(initial);
  const [review, setReview] = useState(initialReview);
  const [showClosed, setShowClosed] = useState(false);
  const [sel, setSel] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [internal, setInternal] = useState(false);
  const [escalation, setEscalation] = useState("");
  const [provider, setProvider] = useState({ email: "", consent: false });
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const cur = tickets.find((t) => t.id === sel) ?? null;
  const visible = tickets.filter((t) => (showClosed || OPEN.has(t.status)) && (!tenantFilter || t.tenant_id === tenantFilter));
  const reload = async () => {
    const [ts, rv] = await Promise.all([fetchDeskTickets(), fetchTagReview(7)]);
    if (ts) setTickets(ts);
    if (rv) setReview(rv);
  };
  const act = async (path: string, body?: unknown) => {
    setBusy(true);
    const r = await request<unknown>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
    setMsg(r.ok ? "Done" : r.error);
    await reload();
    setBusy(false);
    return r.ok;
  };
  const send = async () => {
    if (!cur || !text.trim()) return;
    if (await act(`/v1/admin/ops/support/tickets/${cur.id}/reply`, { text, public: !internal })) setText("");
  };
  const setStatus = (t: SupportTicket, patch: { status?: SupportStatus; assignee?: string | null; priority?: SupportPriority }) =>
    act(`/v1/admin/ops/support/tickets/${t.id}/status`, { status: patch.status ?? t.status, assignee: patch.assignee === undefined ? t.assignee : patch.assignee, priority: patch.priority ?? t.priority });

  return (
    <>
      {review && (
        <div className="grid">
          <Stat label="Tickets (7d)" value={review.tickets} sub={`${review.resolved} resolved`} />
          <Stat label="CSAT" value={review.csat_avg == null ? "—" : `${review.csat_avg.toFixed(1)} / 5`} sub={`${review.csat_responses} responses`} />
          <Stat label="SLA breaches" value={review.sla_breaches} sub={review.median_first_response_min == null ? "no first responses yet" : `median first response ${Math.round(review.median_first_response_min)} min`} />
          <Stat label="Top tags" value={Object.entries(review.by_tag).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([k, v]) => `${k} ${v}`).join(" · ") || "—"} sub="drives product & KB priorities" />
        </div>
      )}
      <div className="row" style={{ marginBottom: "0.8rem" }}>
        {canAct && <button className="ghost" disabled={busy} onClick={() => act("/v1/admin/ops/support/sla-sweep")}>Run SLA sweep</button>}
        <label className="check small"><input type="checkbox" checked={showClosed} onChange={(e) => setShowClosed(e.target.checked)} /> show resolved/closed</label>
        {tenantFilter && <span className="small">Filtered to <strong>{tenantFilter}</strong> · <Link href="/admin/support">clear</Link></span>}
        {msg && <span className="small" style={{ color: "var(--accent)" }}>{msg}</span>}
      </div>
      <div className="grid" style={{ gridTemplateColumns: cur ? "1fr 1.3fr" : "1fr" }}>
        <div className="section">
          <h2>Queue</h2>
          {visible.length === 0 ? <p className="muted small">Queue is empty.</p> : (
            <table>
              <thead><tr><th>Ticket</th><th>Tenant</th><th>Pri</th><th>Status</th><th>Due</th><th>Assignee</th></tr></thead>
              <tbody>
                {visible.map((t) => (
                  <tr key={t.id} onClick={() => setSel(t.id)} style={{ cursor: "pointer", background: t.id === sel ? "var(--hover)" : undefined }}>
                    <td><strong>{t.subject}</strong><div className="small muted">{t.id} · {t.channel} · {t.requester}</div></td>
                    <td className="small">{t.tenant_id}</td>
                    <td><span className={`pill ${prCls(t.priority)}`}>{t.priority.toUpperCase()}</span></td>
                    <td><span className={`pill ${stCls(t.status)}`}>{t.status.replaceAll("_", " ")}</span>{t.sla_breached && <span className="pill bad" style={{ marginLeft: 4 }}>SLA</span>}</td>
                    <td className="small">{t.sla_due_at && OPEN.has(t.status) ? when(t.sla_due_at) : "—"}</td>
                    <td className="small">{t.assignee ?? <span className="muted">unassigned</span>}</td>
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
              <span className="small"><Link href={`/admin/tenants/${cur.tenant_id}`}>{cur.tenant_id}</Link> · <Link href={`/admin/ops`}>health</Link></span>
            </div>
            <p className="small">{cur.body}</p>
            {cur.fault && (
              <div className="card small" style={{ marginBottom: "0.6rem" }}>
                <strong>Fault: {cur.fault.headline}</strong> <span className="pill warn">{cur.fault.attribution.replaceAll("_", " ")} · {Math.round(cur.fault.confidence * 100)}%</span>
                <p>{cur.fault.explanation}</p>
                <details><summary>Provider report</summary><pre style={{ whiteSpace: "pre-wrap" }}>{cur.fault.provider_report}</pre></details>
              </div>
            )}
            {cur.tags.length > 0 && <p className="small">{cur.tags.map((tg) => <span key={tg} className="pill" style={{ marginRight: 4 }}>{tg}</span>)}</p>}
            {cur.engineering_ref && <p className="small">Engineering: <code>{cur.engineering_ref}</code></p>}
            {cur.provider_emailed_at && <p className="small muted">Provider emailed {cur.provider_email} at {when(cur.provider_emailed_at)}</p>}
            {cur.csat_score != null && <p className="small">CSAT {cur.csat_score}/5 {cur.csat_comment && `— “${cur.csat_comment}”`}</p>}
            <TicketEvents t={cur} />
            {canAct && (
              <div className="form" style={{ marginTop: "0.8rem", display: "grid", gap: "0.6rem" }}>
                <div className="row">
                  <select value={cur.status} onChange={(e) => setStatus(cur, { status: e.target.value as SupportStatus })}>{STATUSES.map((s) => <option key={s} value={s}>{s.replaceAll("_", " ")}</option>)}</select>
                  <select value={cur.priority} onChange={(e) => setStatus(cur, { priority: e.target.value as SupportPriority })}>{(Object.keys(PRIORITY) as SupportPriority[]).map((p) => <option key={p} value={p}>{PRIORITY[p]}</option>)}</select>
                  {cur.assignee !== staffName && <button className="ghost" disabled={busy} onClick={() => setStatus(cur, { assignee: staffName })}>Assign to me</button>}
                </div>
                <textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} placeholder={internal ? "Internal note (never shown to the customer)" : "Reply to customer"} />
                <div className="row">
                  <label className="check small"><input type="checkbox" checked={internal} onChange={(e) => setInternal(e.target.checked)} /> internal note</label>
                  <button className="primary" disabled={busy || !text.trim()} onClick={send}>{internal ? "Add note" : "Send reply"}</button>
                </div>
                <details>
                  <summary className="small">Escalate to engineering</summary>
                  <div className="row" style={{ marginTop: "0.4rem" }}>
                    <input value={escalation} onChange={(e) => setEscalation(e.target.value)} placeholder="Summary for the engineering issue" style={{ flex: 1 }} />
                    <button className="ghost" disabled={busy || !escalation.trim()} onClick={() => act(`/v1/admin/ops/support/tickets/${cur.id}/escalate`, { summary: escalation }).then(() => setEscalation(""))}>Escalate</button>
                  </div>
                </details>
                <details>
                  <summary className="small">Email fault report to customer&apos;s provider</summary>
                  <div className="row" style={{ marginTop: "0.4rem" }}>
                    <input type="email" value={provider.email} onChange={(e) => setProvider({ ...provider, email: e.target.value })} placeholder="support@provider.example" style={{ flex: 1 }} />
                    <label className="check small"><input type="checkbox" checked={provider.consent} onChange={(e) => setProvider({ ...provider, consent: e.target.checked })} /> customer consented</label>
                    <button className="ghost" disabled={busy || !provider.email || !provider.consent || !cur.fault} onClick={() => act(`/v1/admin/ops/support/tickets/${cur.id}/provider-email`, { provider_email: provider.email, consent: provider.consent })}>Send</button>
                  </div>
                  {!cur.fault && <p className="small muted">Run a diagnosis first so there is a fault report to send.</p>}
                </details>
              </div>
            )}
          </div>
        )}
      </div>
    </>
  );
}
