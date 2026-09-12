import Link from "next/link";
import { notFound } from "next/navigation";
import { fetchCalls, fetchContact, secs, when } from "@/lib/api";
import ContactForm from "./form";

export const dynamic = "force-dynamic";

export default async function ContactDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const contact = await fetchContact(id);
  if (!contact) notFound();
  const calls = ((await fetchCalls({ q: contact.e164, limit: 50 })) ?? []).filter((c) => c.contact_id === contact.id || c.caller === contact.e164);
  return (
    <>
      <p className="small"><Link href="/contacts">← Contacts</Link></p>
      <h1>{contact.name ?? contact.e164}</h1>
      <div className="grid">
        <div className="card"><div className="label">Calls</div><div className="value">{contact.call_count}</div></div>
        <div className="card"><div className="label">First seen</div><div className="value" style={{ fontSize: "1rem" }}>{when(contact.first_seen_at)}</div></div>
        <div className="card"><div className="label">Last seen</div><div className="value" style={{ fontSize: "1rem" }}>{when(contact.last_seen_at)}</div></div>
      </div>
      <div className="section">
        <h2>Details</h2>
        <ContactForm initial={contact} />
      </div>
      <div className="section">
        <h2>Call history</h2>
        <table>
          <thead><tr><th>When</th><th>Outcome</th><th>Summary</th><th>Duration</th></tr></thead>
          <tbody>
            {calls.map((c) => (
              <tr key={c.call_id}>
                <td><Link href={`/calls/${c.call_id}`}>{when(c.started_at)}</Link></td>
                <td><span className="pill">{c.kind}</span></td>
                <td className="small muted">{c.summary ?? "—"}</td>
                <td>{secs(c.duration_s)}</td>
              </tr>
            ))}
            {!calls.length && <tr><td colSpan={4} className="muted">No calls found</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  );
}
