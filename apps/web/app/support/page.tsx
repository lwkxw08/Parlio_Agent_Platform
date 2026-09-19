import Link from "next/link";
import { redirect } from "next/navigation";
import { fetchKb, fetchMe, fetchSupportTickets } from "@/lib/api";
import Support from "./support";

export const dynamic = "force-dynamic";

export default async function Page({ searchParams }: { searchParams: Promise<{ tenant?: string }> }) {
  const sp = await searchParams;
  const me = await fetchMe();
  if (!me.ok) return <><h1>Support</h1><p className="muted">{me.status === 401 ? <Link href="/login">Sign in</Link> : "API unreachable"}</p></>;
  const active = me.data.memberships.filter((m) => m.status === "active");
  const tenant = sp.tenant ?? active[0]?.tenant_id;
  if (!tenant) redirect(me.data.staff_role ? "/admin" : "/onboarding");
  const [tickets, kb] = await Promise.all([fetchSupportTickets(tenant), fetchKb(tenant)]);
  if (!tickets) return <><h1>Support</h1><p className="muted">API unreachable</p></>;
  return (
    <>
      <h1>Support</h1>
      {active.length > 1 && (
        <div className="chips" style={{ marginBottom: "1rem" }}>
          {active.map((m) => <Link key={m.tenant_id} href={`/support?tenant=${m.tenant_id}`} className={m.tenant_id === tenant ? "active" : ""}>{me.data.organisations[m.tenant_id] ?? m.tenant_id}</Link>)}
        </div>
      )}
      <Support tenant={tenant} tickets={tickets} kb={kb ?? []} />
    </>
  );
}
