import Link from "next/link";
import { redirect } from "next/navigation";
import { fetchConnections, fetchMe, fetchResources, fetchSchedule, fetchSites } from "@/lib/api";
import ScheduleBoard from "./board";

export const dynamic = "force-dynamic";

const today = () => new Date().toISOString().slice(0, 10);

export default async function Schedule({ searchParams }: { searchParams: Promise<{ tenant?: string; start?: string; days?: string }> }) {
  const sp = await searchParams;
  const me = await fetchMe();
  if (!me.ok) return <><h1>Schedule</h1><p className="muted">{me.status === 401 ? <Link href="/login">Sign in</Link> : "API unreachable"}</p></>;
  const active = me.data.memberships.filter((m) => m.status === "active");
  const tenant = sp.tenant ?? active[0]?.tenant_id;
  if (!tenant) redirect(me.data.staff_role ? "/admin" : "/onboarding");
  const start = /^\d{4}-\d{2}-\d{2}$/.test(sp.start ?? "") ? (sp.start as string) : today();
  const days = sp.days === "7" ? 7 : 1;
  const [view, resources, connections, sites] = await Promise.all([
    fetchSchedule(tenant, { start, days }),
    fetchResources(tenant),
    fetchConnections(tenant),
    fetchSites(),
  ]);
  const role = me.data.memberships.find((m) => m.tenant_id === tenant)?.role ?? "viewer";
  return (
    <ScheduleBoard
      tenant={tenant}
      initial={view.ok ? view.data : null}
      error={view.ok ? null : view.error}
      start={start}
      days={days}
      resources={resources ?? []}
      services={(connections ?? []).flatMap((c) => c.rules.services)}
      sites={(sites ?? []).filter((s) => s.assistant_id)}
      canManage={role === "owner" || role === "admin" || role === "member"}
    />
  );
}
