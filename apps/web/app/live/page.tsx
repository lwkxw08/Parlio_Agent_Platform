import Link from "next/link";
import { fetchApprovals, fetchLiveCalls, fetchMe } from "@/lib/api";
import Live from "./live";

export const dynamic = "force-dynamic";

export default async function Page({ searchParams }: { searchParams: Promise<{ tenant?: string }> }) {
  const sp = await searchParams;
  const me = await fetchMe();
  if (!me.ok) return <><h1>Live</h1><p className="muted">{me.status === 401 ? <Link href="/login">Sign in</Link> : "API unreachable"}</p></>;
  const active = me.data.memberships.filter((m) => m.status === "active");
  const tenant = sp.tenant ?? active[0]?.tenant_id;
  if (!tenant) return <><h1>Live</h1><p className="muted">No organisation yet — <Link href="/onboarding">set one up</Link>.</p></>;
  const [calls, approvals] = await Promise.all([fetchLiveCalls(tenant), fetchApprovals(tenant)]);
  const role = me.data.memberships.find((m) => m.tenant_id === tenant)?.role ?? "viewer";
  if (calls === null) return <><h1>Live</h1><p className="muted">API unreachable</p></>;
  return (
    <>
      <h1>Live calls</h1>
      {active.length > 1 && (
        <div className="chips" style={{ marginBottom: "1rem" }}>
          {active.map((m) => <Link key={m.tenant_id} href={`/live?tenant=${m.tenant_id}`} className={m.tenant_id === tenant ? "active" : ""}>{m.tenant_id}</Link>)}
        </div>
      )}
      <Live tenant={tenant} me={me.data.email} canControl={role !== "viewer"} initialCalls={calls} initialApprovals={approvals ?? []} />
    </>
  );
}
