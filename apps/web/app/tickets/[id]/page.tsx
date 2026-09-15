import Link from "next/link";
import { notFound } from "next/navigation";
import { fetchTicket, secs, ticketRef } from "@/lib/api";
import TicketActions from "./actions";
import { humanize } from "@/app/breakdown";

export const dynamic = "force-dynamic";

export default async function TicketPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const detail = await fetchTicket(id);
  if (!detail) notFound();
  const { ticket: t, events, sla_remaining_s } = detail;

  return (
    <>
      <p><Link href="/tickets">← Tickets</Link></p>
      <div className="row" style={{ alignItems: "baseline", gap: "0.75rem", flexWrap: "wrap" }}>
        <h1 style={{ margin: 0 }}>{t.reason}</h1>
        <span className={`pill ${t.priority}`}>{humanize(t.priority)}</span>
        <span className="pill">{humanize(t.status)}</span>
      </div>
      <p className="muted small">
        Ticket {ticketRef(t.id)} · raised {new Date(t.created_at).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" })}
        {t.department ? ` · ${t.department}` : ""} <span title="Full reference" style={{ opacity: 0.6 }}>({t.id})</span>
      </p>
      <TicketActions ticket={t} />
      <div className="grid">
        <div className="card"><div className="label">Status</div><div className="value">{t.status}</div></div>
        <div className="card"><div className="label">Priority</div><div className="value"><span className={`pill ${t.priority}`}>{humanize(t.priority)}</span></div></div>
        <div className="card"><div className="label">SLA</div><div className="value">{t.sla_breached ? "breached" : secs(sla_remaining_s)}</div></div>
        <div className="card"><div className="label">Assigned</div><div className="value">{t.assigned_to ?? "—"}</div></div>
      </div>
      <table>
        <tbody>
          <tr><th>Reason</th><td>{t.reason}</td></tr>
          <tr><th>Caller</th><td>{t.caller_name ?? "—"} {t.caller_number && <a href={`tel:${t.caller_number}`}>{t.caller_number}</a>}</td></tr>
          <tr><th>Category / department</th><td>{t.category ?? "—"} / {t.department ?? "—"}</td></tr>
          <tr><th>Callback window</th><td>{t.callback_window ?? "—"}</td></tr>
          <tr><th>Source</th><td>{t.source}</td></tr>
          <tr><th>Call</th><td>{t.call_id ? <Link href={`/calls/${t.call_id}`}>Open call</Link> : "—"}</td></tr>
          <tr>
            <th>Conversation</th>
            <td>
              {t.thread_id ? (
                <>
                  <Link href={`/inbox?tenant=${t.tenant_id}&thread=${t.thread_id}`}>Open in Inbox</Link>
                  <span className="muted small"> — claiming or resolving here updates the conversation, and vice versa</span>
                </>
              ) : "—"}
            </td>
          </tr>
          <tr><th>Created</th><td>{new Date(t.created_at).toLocaleString("en-GB")}</td></tr>
        </tbody>
      </table>
      <h2>Activity</h2>
      <table>
        <thead><tr><th>When</th><th>Event</th><th>Who</th><th>Note</th></tr></thead>
        <tbody>
          {events.map((e, i) => (
            <tr key={i}>
              <td>{new Date(e.at).toLocaleString("en-GB")}</td>
              <td>{e.type}</td>
              <td>{e.actor ?? "—"}</td>
              <td className="muted">{e.note ?? ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
