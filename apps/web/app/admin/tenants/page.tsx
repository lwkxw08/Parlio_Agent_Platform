import Link from "next/link";
import { fetchAdminPlans, fetchAdminTenants, gbp, when } from "@/lib/api";
import { HealthPill, StatusPill, num } from "../ui";

export const dynamic = "force-dynamic";

const STATUSES = ["trialing", "active", "past_due", "paused", "suspended", "cancelled"];

export default async function Page({ searchParams }: { searchParams: Promise<{ q?: string; status?: string; plan?: string }> }) {
  const sp = await searchParams;
  const [tenants, plans] = await Promise.all([
    fetchAdminTenants({ q: sp.q, sub_status: sp.status, plan_id: sp.plan }),
    fetchAdminPlans(),
  ]);
  if (!tenants) return <p className="muted">Couldn&apos;t load this page — the API rejected the request (e.g. your IP isn&apos;t on the staff allow-list) or didn&apos;t answer.</p>;
  return (
    <>
      <form className="filters" method="get">
        <input name="q" placeholder="Search tenant id or name" defaultValue={sp.q ?? ""} />
        <select name="status" defaultValue={sp.status ?? ""}>
          <option value="">Any status</option>
          {STATUSES.map((s) => <option key={s} value={s}>{s.replace("_", " ")}</option>)}
        </select>
        <select name="plan" defaultValue={sp.plan ?? ""}>
          <option value="">Any plan</option>
          {(plans ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <button type="submit" className="ghost">Filter</button>
        <span className="muted small">{tenants.length} tenant{tenants.length === 1 ? "" : "s"}</span>
      </form>
      <table>
        <thead>
          <tr>
            <th>Tenant</th><th>Plan</th><th>Status</th><th>Health</th><th>Calls (30d)</th><th>Minutes</th><th>Est. bill</th><th>Credit</th><th>Members</th><th>Last call</th><th>Created</th>
          </tr>
        </thead>
        <tbody>
          {tenants.map((t) => (
            <tr key={t.tenant_id}>
              <td>
                <Link href={`/admin/tenants/${t.tenant_id}`}>{t.name}</Link>
                <div className="small muted">{t.tenant_id}{t.flags.length > 0 && ` · ${t.flags.join(", ")}`}</div>
              </td>
              <td>{t.plan_name}</td>
              <td><StatusPill s={t.status} />{t.trial_ends_at && t.status === "trialing" && <div className="small muted">ends {when(t.trial_ends_at)}</div>}</td>
              <td><HealthPill h={t.health} /></td>
              <td>{num(t.calls_period)}</td>
              <td>{num(t.minutes_period, 0)} <span className="muted small">/ {t.minutes_included}</span></td>
              <td>{gbp(t.estimated_total_pence)}</td>
              <td>{t.credit_balance_pence ? gbp(t.credit_balance_pence) : "—"}</td>
              <td>{t.members}</td>
              <td className="small">{t.last_call_at ? when(t.last_call_at) : "—"}</td>
              <td className="small">{when(t.created_at)}</td>
            </tr>
          ))}
          {tenants.length === 0 && <tr><td colSpan={11} className="muted">No tenants match.</td></tr>}
        </tbody>
      </table>
    </>
  );
}
