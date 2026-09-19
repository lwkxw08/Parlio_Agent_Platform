import Link from "next/link";
import { redirect } from "next/navigation";
import { fetchConnections, fetchMe, fetchMembers, fetchResources, fetchScheduler, fetchSites, fetchTeamSettings } from "@/lib/api";
import Members from "./members";
import Resources from "./resources";

export const dynamic = "force-dynamic";

export default async function Team({ searchParams }: { searchParams: Promise<{ tenant?: string; tab?: string }> }) {
  const sp = await searchParams;
  const me = await fetchMe();
  if (!me.ok) return <><h1>Team</h1><p className="muted">{me.status === 401 ? <Link href="/login">Sign in</Link> : "API unreachable"}</p></>;
  const active = me.data.memberships.filter((m) => m.status === "active");
  const tenant = sp.tenant ?? active[0]?.tenant_id;
  if (!tenant) redirect(me.data.staff_role ? "/admin" : "/onboarding");
  const tab = sp.tab === "engineers" ? "engineers" : "members";
  const [members, resources, settings, scheduler, connections, sites] = await Promise.all([
    fetchMembers(tenant),
    fetchResources(tenant),
    fetchTeamSettings(tenant),
    fetchScheduler(tenant),
    fetchConnections(tenant),
    fetchSites(),
  ]);
  const myRole = me.data.memberships.find((m) => m.tenant_id === tenant)?.role ?? "viewer";
  const canManage = myRole === "owner" || myRole === "admin";
  const services = (connections ?? []).flatMap((c) => c.rules.services);
  const q = (t: string) => `/team?tenant=${tenant}&tab=${t}`;
  return (
    <>
      <h1>Team</h1>
      {active.length > 1 && (
        <div className="chips" style={{ marginBottom: "1rem" }}>
          {active.map((m) => <Link key={m.tenant_id} href={`/team?tenant=${m.tenant_id}&tab=${tab}`} className={m.tenant_id === tenant ? "active" : ""}>{me.data.organisations[m.tenant_id] ?? m.tenant_id}</Link>)}
        </div>
      )}
      <div className="tabs" style={{ marginBottom: "1rem" }}>
        <Link href={q("members")} className={tab === "members" ? "active" : ""}>Dashboard users</Link>
        <Link href={q("engineers")} className={tab === "engineers" ? "active" : ""}>Team members &amp; scheduling</Link>
        <Link href={`/schedule?tenant=${tenant}`}>Open schedule →</Link>
      </div>
      {tab === "members" ? (
        <Members tenant={tenant} orgName={me.data.organisations[tenant] ?? tenant} initial={members ?? []} me={me.data.user_id} canManage={canManage} />
      ) : (
        <Resources
          tenant={tenant}
          initial={resources ?? []}
          settings={settings ?? { tenant_id: tenant, policy: "least_loaded", mode: "calendar", emergency_to_on_call: true, round_robin_cursor: 0 }}
          scheduler={scheduler.ok ? scheduler.data : null}
          connections={connections ?? []}
          services={services}
          sites={(sites ?? []).filter((s) => s.assistant_id)}
          canManage={canManage}
        />
      )}
    </>
  );
}
