import Link from "next/link";
import { redirect } from "next/navigation";
import {
  fetchAssistants,
  fetchJurisdictions,
  fetchLeads,
  fetchMe,
  fetchOutboundCalls,
  fetchOutboundPolicy,
  fetchOutboundSummary,
  fetchSuppressions,
} from "@/lib/api";
import Outbound from "./outbound";

export const dynamic = "force-dynamic";

export default async function Page({ searchParams }: { searchParams: Promise<{ tenant?: string }> }) {
  const sp = await searchParams;
  const me = await fetchMe();
  if (!me.ok) return <><h1>Outbound</h1><p className="muted">{me.status === 401 ? <Link href="/login">Sign in</Link> : "API unreachable"}</p></>;
  const active = me.data.memberships.filter((m) => m.status === "active");
  const tenant = sp.tenant ?? active[0]?.tenant_id;
  if (!tenant) redirect(me.data.staff_role ? "/admin" : "/onboarding");
  const [summary, policy, jurisdictions, leads, calls, suppressions, assistants] = await Promise.all([
    fetchOutboundSummary(tenant), fetchOutboundPolicy(tenant), fetchJurisdictions(), fetchLeads(tenant), fetchOutboundCalls(tenant), fetchSuppressions(tenant), fetchAssistants(tenant),
  ]);
  const role = me.data.memberships.find((m) => m.tenant_id === tenant)?.role ?? "viewer";
  if (!policy) return <><h1>Outbound</h1><p className="muted">API unreachable</p></>;
  return (
    <>
      <h1>Outbound &amp; speed-to-lead</h1>
      {active.length > 1 && (
        <div className="chips" style={{ marginBottom: "1rem" }}>
          {active.map((m) => <Link key={m.tenant_id} href={`/outbound?tenant=${m.tenant_id}`} className={m.tenant_id === tenant ? "active" : ""}>{me.data.organisations[m.tenant_id] ?? m.tenant_id}</Link>)}
        </div>
      )}
      <Outbound
        tenant={tenant}
        canManage={role === "owner" || role === "admin"}
        summary={summary}
        policy={policy}
        jurisdictions={jurisdictions ?? {}}
        leads={leads ?? []}
        calls={calls ?? []}
        suppressions={suppressions ?? []}
        assistants={assistants ?? []}
      />
    </>
  );
}
