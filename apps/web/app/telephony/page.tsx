import Link from "next/link";
import { redirect } from "next/navigation";
import { fetchAssistants, fetchGuides, fetchMe, fetchNumbers, fetchTrunks } from "@/lib/api";
import Telephony from "./telephony";

export const dynamic = "force-dynamic";

export default async function Page({ searchParams }: { searchParams: Promise<{ tenant?: string }> }) {
  const sp = await searchParams;
  const me = await fetchMe();
  if (!me.ok) return <><h1>Telephony</h1><p className="muted">{me.status === 401 ? <Link href="/login">Sign in</Link> : "API unreachable"}</p></>;
  const active = me.data.memberships.filter((m) => m.status === "active");
  const tenant = sp.tenant ?? active[0]?.tenant_id;
  if (!tenant) redirect(me.data.staff_role ? "/admin" : "/onboarding");
  const [trunks, guides, assistants, numbers] = await Promise.all([fetchTrunks(tenant), fetchGuides(), fetchAssistants(tenant), fetchNumbers(tenant)]);
  const role = me.data.memberships.find((m) => m.tenant_id === tenant)?.role ?? "viewer";
  return (
    <>
      <h1>Telephony</h1>
      {active.length > 1 && (
        <div className="chips" style={{ marginBottom: "1rem" }}>
          {active.map((m) => <Link key={m.tenant_id} href={`/telephony?tenant=${m.tenant_id}`} className={m.tenant_id === tenant ? "active" : ""}>{me.data.organisations[m.tenant_id] ?? m.tenant_id}</Link>)}
        </div>
      )}
      <Telephony tenant={tenant} canManage={role === "owner" || role === "admin"} trunks={trunks ?? []} guides={guides ?? []} assistants={assistants ?? []} numbers={numbers ?? []} />
    </>
  );
}
