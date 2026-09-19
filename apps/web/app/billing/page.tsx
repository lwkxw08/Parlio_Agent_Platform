import Link from "next/link";
import { redirect } from "next/navigation";
import { fetchAssistants, fetchEntitlements, fetchLatency, fetchMe, fetchNumbers, fetchPlans, fetchSubscription, fetchUsage } from "@/lib/api";
import Billing from "./billing";

export const dynamic = "force-dynamic";

export default async function Page({ searchParams }: { searchParams: Promise<{ tenant?: string; tab?: string }> }) {
  const sp = await searchParams;
  const me = await fetchMe();
  if (!me.ok) return <><h1>Billing & usage</h1><p className="muted">{me.status === 401 ? <Link href="/login">Sign in</Link> : "API unreachable"}</p></>;
  const active = me.data.memberships.filter((m) => m.status === "active");
  const tenant = sp.tenant ?? active[0]?.tenant_id;
  if (!tenant) redirect(me.data.staff_role ? "/admin" : "/onboarding");
  const [plans, sub, usage, numbers, latency, assistants, entitlements] = await Promise.all([
    fetchPlans(), fetchSubscription(tenant), fetchUsage(tenant), fetchNumbers(tenant), fetchLatency(tenant, 7), fetchAssistants(tenant), fetchEntitlements(tenant),
  ]);
  const role = me.data.memberships.find((m) => m.tenant_id === tenant)?.role ?? "viewer";
  return (
    <>
      <h1>Billing & usage</h1>
      {active.length > 1 && (
        <div className="chips" style={{ marginBottom: "1rem" }}>
          {active.map((m) => <Link key={m.tenant_id} href={`/billing?tenant=${m.tenant_id}`} className={m.tenant_id === tenant ? "active" : ""}>{me.data.organisations[m.tenant_id] ?? m.tenant_id}</Link>)}
        </div>
      )}
      {!usage || !sub ? (
        <p className="muted">Billing data unavailable.</p>
      ) : (
        <Billing
          tenant={tenant}
          canManage={role === "owner" || role === "admin"}
          initialTab={sp.tab ?? "usage"}
          plans={plans ?? []}
          subscription={sub}
          entitlements={entitlements}
          usage={usage}
          numbers={numbers ?? []}
          latency={latency}
          assistants={assistants ?? []}
        />
      )}
    </>
  );
}
