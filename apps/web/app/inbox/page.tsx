import Link from "next/link";
import { redirect } from "next/navigation";
import { fetchCanned, fetchInboxStats, fetchMe, fetchMembers, fetchThreads, fetchWhatsApp, fetchWidget } from "@/lib/api";
import Inbox from "./inbox";

export const dynamic = "force-dynamic";

export default async function Page({ searchParams }: { searchParams: Promise<{ tenant?: string; thread?: string }> }) {
  const sp = await searchParams;
  const me = await fetchMe();
  if (!me.ok) return <><h1>Inbox</h1><p className="muted">{me.status === 401 ? <Link href="/login">Sign in</Link> : "API unreachable"}</p></>;
  const active = me.data.memberships.filter((m) => m.status === "active");
  const tenant = sp.tenant ?? active[0]?.tenant_id;
  if (!tenant) redirect(me.data.staff_role ? "/admin" : "/onboarding");
  const role = me.data.memberships.find((m) => m.tenant_id === tenant)?.role ?? "viewer";
  const isAdmin = role === "owner" || role === "admin";
  const [threads, stats, canned, members, widget, whatsapp] = await Promise.all([
    fetchThreads(tenant),
    fetchInboxStats(tenant),
    fetchCanned(tenant),
    fetchMembers(tenant),
    isAdmin ? fetchWidget(tenant) : Promise.resolve(null),
    isAdmin ? fetchWhatsApp(tenant) : Promise.resolve(null),
  ]);
  if (threads === null || stats === null) return <><h1>Inbox</h1><p className="muted">API unreachable</p></>;
  return (
    <>
      <h1>Inbox</h1>
      {active.length > 1 && (
        <div className="chips" style={{ marginBottom: "1rem" }}>
          {active.map((m) => <Link key={m.tenant_id} href={`/inbox?tenant=${m.tenant_id}`} className={m.tenant_id === tenant ? "active" : ""}>{me.data.organisations[m.tenant_id] ?? m.tenant_id}</Link>)}
        </div>
      )}
      <Inbox
        tenant={tenant}
        me={me.data.email}
        canReply={role !== "viewer"}
        isAdmin={isAdmin}
        initialThreads={threads}
        initialStats={stats}
        initialCanned={canned ?? []}
        members={(members ?? []).filter((m) => m.status === "active")}
        initialWidget={widget}
        initialWhatsApp={whatsapp}
        initialSelected={sp.thread ?? null}
      />
    </>
  );
}
