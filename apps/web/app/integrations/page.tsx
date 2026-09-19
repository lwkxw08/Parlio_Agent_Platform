import Link from "next/link";
import { redirect } from "next/navigation";
import {
  fetchApiKeys,
  fetchAssistants,
  fetchBookings,
  fetchConnections,
  fetchConnectors,
  fetchMe,
  fetchMessages,
  fetchNotificationLog,
  fetchProviders,
  fetchReminderPolicy,
  fetchReminders,
  fetchRules,
  fetchSyncJobs,
  fetchSyncLog,
} from "@/lib/api";
import Integrations from "./integrations";

export const dynamic = "force-dynamic";

export default async function Page({ searchParams }: { searchParams: Promise<{ tenant?: string; tab?: string; connector?: string; reason?: string }> }) {
  const sp = await searchParams;
  const me = await fetchMe();
  if (!me.ok) return <><h1>Integrations</h1><p className="muted">{me.status === 401 ? <Link href="/login">Sign in</Link> : "API unreachable"}</p></>;
  const active = me.data.memberships.filter((m) => m.status === "active");
  const tenant = sp.tenant ?? active[0]?.tenant_id;
  if (!tenant) redirect(me.data.staff_role ? "/admin" : "/onboarding");
  const role = me.data.memberships.find((m) => m.tenant_id === tenant)?.role ?? "viewer";
  const canManage = role === "owner" || role === "admin";
  const [messages, rules, log, connections, bookings, sync, assistants, providers, connectors, jobs, apiKeys, reminderPolicy, reminders] = await Promise.all([
    fetchMessages(tenant), fetchRules(tenant), fetchNotificationLog(tenant),
    fetchConnections(tenant), fetchBookings(tenant), fetchSyncLog(tenant), fetchAssistants(tenant),
    fetchProviders(), fetchConnectors(tenant), fetchSyncJobs(tenant), canManage ? fetchApiKeys(tenant) : Promise.resolve([]),
    fetchReminderPolicy(tenant), fetchReminders(tenant),
  ]);
  const banner = sp.connector === "connected" ? "Connected — send a sample to check it works."
    : sp.connector === "error" ? `Connection failed (${sp.reason ?? "unknown"}). Try again.` : null;
  return (
    <>
      <h1>Integrations</h1>
      {active.length > 1 && (
        <div className="chips" style={{ marginBottom: "1rem" }}>
          {active.map((m) => <Link key={m.tenant_id} href={`/integrations?tenant=${m.tenant_id}`} className={m.tenant_id === tenant ? "active" : ""}>{me.data.organisations[m.tenant_id] ?? m.tenant_id}</Link>)}
        </div>
      )}
      <Integrations
        tenant={tenant}
        tab={sp.tab ?? "connectors"}
        canManage={canManage}
        messages={messages ?? []}
        rules={rules ?? []}
        log={log ?? []}
        connections={connections ?? []}
        bookings={bookings ?? []}
        sync={sync ?? []}
        assistants={assistants ?? []}
        providers={providers?.providers ?? []}
        payloadFields={providers?.payload_fields ?? []}
        connectors={connectors ?? []}
        jobs={jobs ?? []}
        apiKeys={apiKeys ?? []}
        banner={banner}
        reminderPolicy={reminderPolicy}
        reminders={reminders ?? []}
      />
    </>
  );
}
