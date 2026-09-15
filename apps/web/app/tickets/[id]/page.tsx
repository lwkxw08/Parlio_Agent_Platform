import Link from "next/link";
import { notFound } from "next/navigation";
import { fetchTicket, secs } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function TicketPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const detail = await fetchTicket(id);
  if (!detail) notFound();
  const { ticket: t, events, sla_remaining_s } = detail;

  return (
    <>
      <h1>Ticket {t.id}</h1>
      <div className="grid">
        <div className="card"><div className="label">Status</div><div className="value">{t.status}</div></div>
        <div className="card"><div className="label">Priority</div><div className="value"><span className={`pill ${t.priority}`}>{t.priority}</span></div></div>
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
          <tr><th>Call</th><td>{t.call_id ? <Link href={`/calls/${t.call_id}`}>{t.call_id}</Link> : "—"}</td></tr>
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
      <h1>Activity</h1>
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
