import Link from "next/link";
import {
  fetchAssistants,
  fetchBookings,
  fetchConnections,
  fetchMe,
  fetchMessages,
  fetchNotificationLog,
  fetchRules,
  fetchSyncLog,
} from "@/lib/api";
import Integrations from "./integrations";

export const dynamic = "force-dynamic";

export default async function Page({ searchParams }: { searchParams: Promise<{ tenant?: string; tab?: string }> }) {
  const sp = await searchParams;
  const me = await fetchMe();
  if (!me.ok) return <><h1>Integrations</h1><p className="muted">{me.status === 401 ? <Link href="/login">Sign in</Link> : "API unreachable"}</p></>;
  const active = me.data.memberships.filter((m) => m.status === "active");
  const tenant = sp.tenant ?? active[0]?.tenant_id;
  if (!tenant) return <><h1>Integrations</h1><p className="muted">No organisation yet — <Link href="/onboarding">set one up</Link>.</p></>;
  const [messages, rules, log, connections, bookings, sync, assistants] = await Promise.all([
    fetchMessages(tenant), fetchRules(tenant), fetchNotificationLog(tenant),
    fetchConnections(tenant), fetchBookings(tenant), fetchSyncLog(tenant), fetchAssistants(tenant),
  ]);
  const role = me.data.memberships.find((m) => m.tenant_id === tenant)?.role ?? "viewer";
  return (
    <>
      <h1>Integrations</h1>
      {active.length > 1 && (
        <div className="chips" style={{ marginBottom: "1rem" }}>
          {active.map((m) => <Link key={m.tenant_id} href={`/integrations?tenant=${m.tenant_id}`} className={m.tenant_id === tenant ? "active" : ""}>{m.tenant_id}</Link>)}
        </div>
      )}
      <Integrations
        tenant={tenant}
        tab={sp.tab ?? "notifications"}
        canManage={role === "owner" || role === "admin"}
        messages={messages ?? []}
        rules={rules ?? []}
        log={log ?? []}
        connections={connections ?? []}
        bookings={bookings ?? []}
        sync={sync ?? []}
        assistants={assistants ?? []}
      />
    </>
  );
}
