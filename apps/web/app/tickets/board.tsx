"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { fetchTickets, post, secs, ticketRef, type OutboundCall, type Ticket, type TicketStatus } from "@/lib/api";
import { humanize } from "@/app/breakdown";

const COLUMNS: { status: TicketStatus; title: string }[] = [
  { status: "open", title: "Open" },
  { status: "claimed", title: "Claimed" },
  { status: "resolved", title: "Resolved" },
];

const PRIORITY_ORDER: Record<Ticket["priority"], number> = { urgent: 0, high: 1, normal: 2, low: 3 };

function slaLabel(t: Ticket, now: number) {
  if (!t.sla_due_at || t.status === "resolved" || t.status === "cancelled") return null;
  const remaining = (new Date(t.sla_due_at).getTime() - now) / 1000;
  const cls = t.sla_breached || remaining < 0 ? "bad" : remaining < 900 ? "warn" : "ok";
  return <span className={`pill ${cls}`}>{remaining < 0 ? `overdue ${secs(-remaining)}` : `SLA ${secs(remaining)}`}</span>;
}

export default function TicketBoard({ initial }: { initial: Ticket[] }) {
  const [tickets, setTickets] = useState<Ticket[]>(initial);
  const [actor, setActor] = useState("me");
  const [now, setNow] = useState(() => Date.now());
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    const tick = setInterval(() => setNow(Date.now()), 10_000);
    const poll = setInterval(async () => {
      const fresh = await fetchTickets();
      if (fresh) setTickets(fresh);
    }, 15_000);
    return () => { clearInterval(tick); clearInterval(poll); };
  }, []);

  const act = async (id: string, action: "claim" | "resolve" | "callback", note?: string) => {
    const res = await post<Ticket | { tel_uri: string }>(`/v1/tickets/${id}/${action}`, { actor, note });
    if (res && "tel_uri" in res) window.location.href = res.tel_uri;
    const fresh = await fetchTickets();
    if (fresh) setTickets(fresh);
  };
  const aiCallback = async (t: Ticket) => {
    const r = await post<OutboundCall>(`/v1/outbound/calls?tenant_id=${t.tenant_id}`, {
      purpose: "ticket_callback", to: t.caller_number, name: t.caller_name, ticket_id: t.id,
      context: { reason: t.reason, callback_window: t.callback_window ?? "" },
    });
    setNotice(r ? `AI will call ${t.caller_number} at ${new Date(r.scheduled_at).toLocaleString("en-GB", { dateStyle: "short", timeStyle: "short" })}` : "Could not schedule (outbound disabled, outside calling hours or number on do-not-call list)");
    setTimeout(() => setNotice(null), 6000);
  };

  return (
    <>
      <p className="muted">
        Acting as <input value={actor} onChange={(e) => setActor(e.target.value)} className="inline" />
      </p>
      {notice && <p className="muted small">{notice}</p>}
      <div className="board">
        {COLUMNS.map(({ status, title }) => {
          const col = tickets
            .filter((t) => t.status === status)
            .sort((a, b) => PRIORITY_ORDER[a.priority] - PRIORITY_ORDER[b.priority] || a.created_at.localeCompare(b.created_at));
          return (
            <section key={status} className="column">
              <h2>{title} <span className="muted">{col.length}</span></h2>
              {col.map((t) => (
                <article key={t.id} className={`ticket ${t.priority}`}>
                  <div className="row">
                    <span className="muted small">{ticketRef(t.id)}</span>
                    <span className={`pill ${t.priority}`}>{humanize(t.priority)}</span>
                    {t.category && <span className="pill">{humanize(t.category)}</span>}
                    {t.department && <span className="pill">{humanize(t.department)}</span>}
                    {slaLabel(t, now)}
                  </div>
                  <Link href={`/tickets/${t.id}`} className="reason">{t.reason}</Link>
                  <div className="muted small">
                    {t.caller_name ?? "Unknown caller"}{t.caller_number ? ` · ${t.caller_number}` : ""}
                    {t.callback_window ? ` · callback ${t.callback_window}` : ""}
                    {t.assigned_to ? ` · ${t.assigned_to}` : ""}
                    {t.thread_id && (
                      <>
                        {" · "}
                        <Link href={`/inbox?tenant=${t.tenant_id}&thread=${t.thread_id}`} title="Linked conversation — progress here or there, both stay in step">
                          Open in Inbox
                        </Link>
                      </>
                    )}
                  </div>
                  <div className="row actions">
                    {status === "open" && <button onClick={() => act(t.id, "claim")}>Claim</button>}
                    {status !== "resolved" && t.caller_number && (
                      <button onClick={() => act(t.id, "callback")}>Call back</button>
                    )}
                    {status !== "resolved" && t.caller_number && (
                      <button onClick={() => aiCallback(t)} title="Schedule the assistant to ring them back (Outbound policy applies)">AI call back</button>
                    )}
                    {status !== "resolved" && <button onClick={() => act(t.id, "resolve")}>Resolve</button>}
                  </div>
                </article>
              ))}
              {!col.length && <p className="muted small">Nothing here</p>}
            </section>
          );
        })}
      </div>
    </>
  );
}
