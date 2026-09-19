import Link from "next/link";
import { redirect } from "next/navigation";
import { fetchMe, fetchValue } from "@/lib/api";
import Value from "./value";

export const dynamic = "force-dynamic";

export default async function Page({ searchParams }: { searchParams: Promise<{ tenant?: string; days?: string }> }) {
  const sp = await searchParams;
  const me = await fetchMe();
  if (!me.ok) return <><h1>Value</h1><p className="muted">{me.status === 401 ? <Link href="/login">Sign in</Link> : "API unreachable"}</p></>;
  const active = me.data.memberships.filter((m) => m.status === "active");
  const tenant = sp.tenant ?? active[0]?.tenant_id;
  if (!tenant) redirect(me.data.staff_role ? "/admin" : "/onboarding");
  const days = Number(sp.days ?? 7) || 7;
  const role = me.data.memberships.find((m) => m.tenant_id === tenant)?.role ?? "viewer";
  const overview = await fetchValue(tenant, days);
  if (!overview) return <><h1>Value</h1><p className="muted">API unreachable</p></>;
  return (
    <>
      <h1>Business value</h1>
      {active.length > 1 && (
        <div className="chips" style={{ marginBottom: "1rem" }}>
          {active.map((m) => <Link key={m.tenant_id} href={`/value?tenant=${m.tenant_id}`} className={m.tenant_id === tenant ? "active" : ""}>{me.data.organisations[m.tenant_id] ?? m.tenant_id}</Link>)}
        </div>
      )}
      <Value tenant={tenant} days={days} canManage={role === "owner" || role === "admin"} overview={overview} />
    </>
  );
}
