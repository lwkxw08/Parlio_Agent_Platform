import Link from "next/link";
import { redirect } from "next/navigation";
import { fetchAudit, fetchMe, fetchRetention } from "@/lib/api";
import Compliance from "./compliance";

export const dynamic = "force-dynamic";

export default async function Page({ searchParams }: { searchParams: Promise<{ tenant?: string }> }) {
  const sp = await searchParams;
  const me = await fetchMe();
  if (!me.ok) return <><h1>Compliance</h1><p className="muted">{me.status === 401 ? <Link href="/login">Sign in</Link> : "API unreachable"}</p></>;
  const active = me.data.memberships.filter((m) => m.status === "active");
  const tenant = sp.tenant ?? active[0]?.tenant_id;
  if (!tenant) redirect(me.data.staff_role ? "/admin" : "/onboarding");
  const role = me.data.memberships.find((m) => m.tenant_id === tenant)?.role ?? "viewer";
  const canManage = role === "owner" || role === "admin";
  const [retention, audit] = await Promise.all([fetchRetention(tenant), canManage ? fetchAudit(tenant) : Promise.resolve(null)]);
  return (
    <>
      <h1>Compliance</h1>
      {active.length > 1 && (
        <div className="chips" style={{ marginBottom: "1rem" }}>
          {active.map((m) => <Link key={m.tenant_id} href={`/compliance?tenant=${m.tenant_id}`} className={m.tenant_id === tenant ? "active" : ""}>{me.data.organisations[m.tenant_id] ?? m.tenant_id}</Link>)}
        </div>
      )}
      {!retention ? <p className="muted">Compliance data unavailable.</p> : (
        <Compliance tenant={tenant} canManage={canManage} retention={retention} audit={audit ?? []} />
      )}
    </>
  );
}
