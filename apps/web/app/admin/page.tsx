import Link from "next/link";
import { fetchAdminOverview, gbp, when } from "@/lib/api";
import { Bars, Mix, Stat, num, pct, secs } from "./ui";

export const dynamic = "force-dynamic";

export default async function Page() {
  const r = await fetchAdminOverview(30);
  if (!r.ok) return <p className="muted">{r.error}</p>;
  const { status, analytics: a, staff, recent_audit } = r.data;
  const b = a.business;
  const d = a.demand;
  const q = a.quality;
  return (
    <>
      {status.level !== "ok" && (
        <p className="small">
          Platform banner: <span className="pill warn">{status.level}</span> {status.title}
          {status.starts_at && new Date(status.starts_at).getTime() > Date.now() && ` (scheduled from ${when(status.starts_at)})`}
          {status.ends_at && new Date(status.ends_at).getTime() <= Date.now() && ` (expired ${when(status.ends_at)})`}
          {" — "}<Link href="/admin/status">manage</Link>
        </p>
      )}
      <div className="grid">
        <Stat label="Tenants" value={num(b.tenants)} sub={`${b.signups_period} new · ${b.trialing} trialing · ${b.active} active`} />
        <Stat label="MRR" value={gbp(b.mrr_pence)} sub={`ARR ${gbp(b.arr_pence)} · overage ${gbp(b.overage_pence_period)} (30d)`} />
        <Stat label="Trial conversion" value={pct(b.trial_conversion_pct)} sub={`${b.conversions_period} converted · ${b.churned_period} churned`} />
        <Stat label="Calls (30d)" value={num(d.calls)} sub={`${num(d.minutes, 0)} min · ${d.missed} missed · ${d.failed} failed`} />
        <Stat label="Peak concurrency" value={`${d.peak_concurrency} / ${d.capacity_concurrent}`} sub="peak vs sold capacity" />
        <Stat label="Answer latency p95" value={secs(q.answer_latency_p95_s)} sub={`p50 ${secs(q.answer_latency_p50_s)}`} />
        <Stat label="Gross margin" value={pct(q.gross_margin_pct)} sub={`vendor ${gbp(Math.round(q.vendor_cost_pence))} · revenue ${gbp(q.revenue_pence)}`} />
        <Stat label="Staff" value={num(staff)} sub={<Link href="/admin/staff">manage</Link>} />
      </div>
      <div className="grid" style={{ gridTemplateColumns: "2fr 1fr" }}>
        <div className="card">
          <h2>Calls per day</h2>
          <Bars points={d.calls_by_day} tall />
        </div>
        <div className="card">
          <h2>Plan mix</h2>
          <Mix data={b.plan_mix} />
          <h2 style={{ marginTop: "1rem" }}>Tenant health</h2>
          <Mix data={q.tenant_health} />
        </div>
      </div>
      <div className="grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
        <div className="card">
          <h2>Top accounts (30d)</h2>
          {b.top_accounts.length === 0 ? <p className="muted small">No usage yet.</p> : (
            <table>
              <thead><tr><th>Tenant</th><th>Plan</th><th>Minutes</th><th>Est. bill</th></tr></thead>
              <tbody>
                {b.top_accounts.map((t) => (
                  <tr key={String(t.tenant_id)}>
                    <td><Link href={`/admin/tenants/${String(t.tenant_id)}`}>{String(t.name ?? t.tenant_id)}</Link></td>
                    <td>{String(t.plan_id ?? "")}</td>
                    <td>{num(Number(t.minutes ?? 0), 0)}</td>
                    <td>{gbp(Number(t.estimated_total_pence ?? 0))}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        <div className="card">
          <h2>Recent staff activity</h2>
          {recent_audit.length === 0 ? <p className="muted small">Nothing yet.</p> : (
            <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
              {recent_audit.slice(0, 12).map((e) => (
                <li key={e.id} className="small row between">
                  <span><strong>{e.actor}</strong> {e.action} <span className="muted">{e.target ?? e.tenant_id}</span></span>
                  <span className="muted">{when(e.at)}</span>
                </li>
              ))}
            </ul>
          )}
          <p className="small"><Link href="/admin/activity">Full activity log</Link></p>
        </div>
      </div>
    </>
  );
}
