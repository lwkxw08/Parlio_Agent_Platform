import Link from "next/link";
import { redirect } from "next/navigation";
import { fetchClients, fetchCompliancePack, fetchMe, fetchSecurity, fetchWhiteLabel } from "@/lib/api";
import Settings from "./settings";

export const dynamic = "force-dynamic";

export default async function Page({ searchParams }: { searchParams: Promise<{ tenant?: string; tab?: string }> }) {
  const sp = await searchParams;
  const me = await fetchMe();
  if (!me.ok) return <><h1>Organisation settings</h1><p className="muted">{me.status === 401 ? <Link href="/login">Sign in</Link> : "API unreachable"}</p></>;
  const active = me.data.memberships.filter((m) => m.status === "active");
  const tenant = sp.tenant ?? active[0]?.tenant_id;
  if (!tenant) redirect(me.data.staff_role ? "/admin" : "/onboarding");
  const role = me.data.memberships.find((m) => m.tenant_id === tenant)?.role ?? "viewer";
  const [wl, clients, pack, sec] = await Promise.all([fetchWhiteLabel(tenant), fetchClients(tenant), fetchCompliancePack(tenant), fetchSecurity(tenant)]);
  if (!wl) return <><h1>Organisation settings</h1><p className="muted">API unreachable</p></>;
  return (
    <>
      <h1>Organisation settings</h1>
      {active.length > 1 && (
        <div className="chips" style={{ marginBottom: "1rem" }}>
          {active.map((m) => <Link key={m.tenant_id} href={`/settings?tenant=${m.tenant_id}`} className={m.tenant_id === tenant ? "active" : ""}>{me.data.organisations[m.tenant_id] ?? m.tenant_id}</Link>)}
        </div>
      )}
      <Settings
        tenant={tenant}
        initialTab={sp.tab}
        canManage={role === "owner" || role === "admin"}
        isOwner={role === "owner"}
        whitelabel={wl}
        clients={clients ?? []}
        pack={pack}
        security={sec.ok ? sec.data : null}
        securityError={sec.ok ? null : sec.error}
        mfaRequired={!sec.ok && sec.status === 403 && sec.error.toLowerCase().includes("two-factor")}
      />
    </>
  );
}
