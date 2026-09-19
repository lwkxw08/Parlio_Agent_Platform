import Link from "next/link";
import { redirect } from "next/navigation";
import { fetchAssistants, fetchHealth, fetchMe, fetchSnapshot } from "@/lib/api";
import LiveSnapshot from "./snapshot";
import { humanize } from "@/app/breakdown";

export const dynamic = "force-dynamic";

export default async function Overview() {
  const [health, me, assistants] = await Promise.all([fetchHealth(), fetchMe(), fetchAssistants()]);
  const tenant = me.ok ? me.data.memberships.find((m) => m.status === "active")?.tenant_id ?? null : null;
  if (me.ok && !tenant && !me.data.staff_role) redirect("/onboarding");
  const snapshot = tenant ? await fetchSnapshot(tenant) : null;

  return (
    <>
      <div className="row between">
        <h1 style={{ margin: 0 }}>Overview</h1>
        <span className={`pill ${health ? "ok" : "bad"}`}>
          {health ? "Assistant online" : "Assistant offline — calls will not be answered"}
        </span>
      </div>
      <p className="muted small" style={{ margin: "0.4rem 0 1rem" }}>
        A live picture of what your assistant and team are dealing with. Click any card to open it.
      </p>

      {tenant ? (
        <LiveSnapshot tenantId={tenant} initial={snapshot} />
      ) : (
        <p className="muted">
          {me.ok ? <>No organisation yet — <Link href="/onboarding">set one up</Link>.</> : <Link href="/login">Sign in</Link>}
        </p>
      )}

      <h2>Assistants</h2>
      <table>
        <thead><tr><th>Name</th><th>Business</th><th>Language</th><th>Region</th><th>Greeting</th></tr></thead>
        <tbody>
          {(assistants ?? []).map((a) => (
            <tr key={a.assistant_id}>
              <td><Link href="/assistant">{a.name}</Link></td><td>{a.business_name}</td><td>{a.language}</td>
              <td><span className="pill">{humanize(a.region_profile)}</span></td>
              <td className="muted">{a.greeting}</td>
            </tr>
          ))}
          {!assistants?.length && <tr><td colSpan={5} className="muted">No assistants (API offline?)</td></tr>}
        </tbody>
      </table>
    </>
  );
}
