import Link from "next/link";
import { redirect } from "next/navigation";
import { fetchChecklist, fetchFirstWeek, fetchMe, fetchPlans, fetchQuestionnaire, fetchWhiteGlove, fetchWhiteGloveAreas, fetchWidget } from "@/lib/api";
import { FirstWeekCard, WhiteGloveCard } from "./extras";
import Setup from "./setup";

export const dynamic = "force-dynamic";

export default async function Page({ searchParams }: { searchParams: Promise<{ tenant?: string; checkout?: string }> }) {
  const sp = await searchParams;
  const me = await fetchMe();
  if (!me.ok) return <><h1>Setup</h1><p className="muted">{me.status === 401 ? <Link href="/login">Sign in</Link> : "API unreachable"}</p></>;
  const active = me.data.memberships.filter((m) => m.status === "active");
  const tenant = sp.tenant ?? active[0]?.tenant_id;
  if (!tenant) redirect(me.data.staff_role ? "/admin" : "/onboarding");
  const [checklist, plans, q, widget, week, wg, areas] = await Promise.all([
    fetchChecklist(tenant), fetchPlans(), fetchQuestionnaire(tenant), fetchWidget(tenant), fetchFirstWeek(tenant), fetchWhiteGlove(tenant), fetchWhiteGloveAreas(),
  ]);
  if (!checklist) return <><h1>Setup</h1><p className="muted">API unreachable</p></>;
  const role = me.data.memberships.find((m) => m.tenant_id === tenant)?.role ?? "viewer";
  const canManage = role === "owner" || role === "admin";
  return (
    <>
      <h1>Setup checklist</h1>
      {active.length > 1 && (
        <div className="chips" style={{ marginBottom: "1rem" }}>
          {active.map((m) => <Link key={m.tenant_id} href={`/setup?tenant=${m.tenant_id}`} className={m.tenant_id === tenant ? "active" : ""}>{me.data.organisations[m.tenant_id] ?? m.tenant_id}</Link>)}
        </div>
      )}
      {sp.checkout === "simulated" && <div className="banner" style={{ marginBottom: "1rem" }}><strong>Payment details saved (simulated checkout)</strong><span>Your subscription is active.</span></div>}
      <Setup tenant={tenant} initial={checklist} plans={plans ?? []} questionnaire={q?.questionnaire ?? null} widget={widget} canManage={canManage} />
      {week && (week.calls > 0 || checklist.live) && <FirstWeekCard report={week} tenant={tenant} />}
      {wg && <WhiteGloveCard tenant={tenant} initial={wg} areas={areas ?? {}} canManage={canManage} />}
    </>
  );
}
