import Link from "next/link";
import { fetchAssistants, fetchContactRules, fetchContacts, when } from "@/lib/api";
import ContactRulesForm from "./rules";
import { humanize } from "@/app/breakdown";

export const dynamic = "force-dynamic";

export default async function Contacts({ searchParams }: { searchParams: Promise<{ q?: string; status?: string }> }) {
  const sp = await searchParams;
  const [all, rules, assistants] = await Promise.all([fetchContacts({ q: sp.q }), fetchContactRules(), fetchAssistants()]);
  const departments = Array.from(new Set((assistants ?? []).flatMap((a) => a.transfer.destinations.map((d) => d.department)))).sort();
  const gbp = (pence: number) => `£${Math.round(pence / 100).toLocaleString("en-GB")}`;
  const contacts = sp.status ? (all ?? []).filter((c) => (sp.status === "vip" ? c.vip : c.status === sp.status)) : all ?? [];
  const chip = (s: string | undefined, label: string) => (
    <Link key={label} href={`/contacts${s ? `?status=${s}` : ""}${sp.q ? `${s ? "&" : "?"}q=${encodeURIComponent(sp.q)}` : ""}`} className={(sp.status ?? "") === (s ?? "") ? "active" : ""}>{label}</Link>
  );
  return (
    <>
      <h1>Contacts</h1>
      <p className="muted small">Every caller is remembered so the assistant can greet returning callers by name, know their history and prioritise VIPs.</p>
      {rules && <ContactRulesForm initial={rules} departments={departments} />}
      <div className="chips" style={{ marginBottom: "0.8rem" }}>
        {chip(undefined, "All")}{chip("prospect", "Prospects")}{chip("customer", "Customers")}{chip("vip", "VIP")}{chip("blocked", "Blocked")}
      </div>
      <form className="filters" action="/contacts" method="get">
        {sp.status && <input type="hidden" name="status" value={sp.status} />}
        <input name="q" placeholder="Search name, number or email" defaultValue={sp.q} />
        <button className="ghost">Search</button>
      </form>
      <table>
        <thead><tr><th>Name</th><th>Number</th><th>Status</th><th>Value</th><th>Calls</th><th>First seen</th><th>Last seen</th></tr></thead>
        <tbody>
          {contacts.map((c) => (
            <tr key={c.id}>
              <td><Link href={`/contacts/${c.id}`}>{c.name ?? <span className="muted">Unknown</span>}</Link>{c.vip && <span className="pill warn" style={{ marginLeft: 4 }}>VIP</span>}</td>
              <td>{c.e164}</td>
              <td>
                <span className={`pill ${c.status === "customer" ? "ok" : c.status === "blocked" ? "bad" : ""}`}>{humanize(c.status)}</span>
                {c.status_pinned && <span className="muted small" title="Set by hand — automatic rules won't change it" style={{ marginLeft: 4 }}>manual</span>}
              </td>
              <td>{c.lifetime_value_pence ? gbp(c.lifetime_value_pence) : <span className="muted">—</span>}</td>
              <td>{c.call_count}{c.call_count > 1 && <span className="muted small"> returning</span>}</td>
              <td>{when(c.first_seen_at)}</td>
              <td>{when(c.last_seen_at)}</td>
            </tr>
          ))}
          {!contacts.length && <tr><td colSpan={7} className="muted">No contacts yet — they appear automatically after the first call.</td></tr>}
        </tbody>
      </table>
    </>
  );
}
