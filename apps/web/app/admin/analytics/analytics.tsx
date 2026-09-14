"use client";

import Link from "next/link";
import { useState } from "react";
import { type PlatformAnalytics, fetchAdminCsv, gbp } from "@/lib/api";
import { Bars, Mix, Stat, TenantLink, millis, num, pct, secs } from "../ui";

const SECTIONS = [["business", "Business"], ["demand", "Demand"], ["quality", "Quality & cost"]] as const;
type Section = (typeof SECTIONS)[number][0];

function download(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

export default function Analytics({ a }: { a: PlatformAnalytics }) {
  const [section, setSection] = useState<Section>("business");
  const [busy, setBusy] = useState(false);
  const exportCsv = async (what: "tenants" | "analytics") => {
    setBusy(true);
    const csv = await fetchAdminCsv(what, a.days);
    if (csv) download(`parlio-${what}-${a.days}d.csv`, csv);
    setBusy(false);
  };
  const b = a.business;
  const d = a.demand;
  const q = a.quality;
  return (
    <>
      <div className="row between" style={{ marginBottom: "1rem" }}>
        <div className="chips">
          {[7, 30, 90].map((n) => <Link key={n} href={`/admin/analytics?days=${n}`} className={a.days === n ? "active" : ""}>{n}d</Link>)}
        </div>
        <div className="row">
          <button type="button" className="ghost" disabled={busy} onClick={() => exportCsv("analytics")}>Export analytics CSV</button>
          <button type="button" className="ghost" disabled={busy} onClick={() => exportCsv("tenants")}>Export tenants CSV</button>
        </div>
      </div>
      <div className="tabs">
        {SECTIONS.map(([id, label]) => <button key={id} type="button" className={section === id ? "active" : ""} onClick={() => setSection(id)}>{label}</button>)}
      </div>

      {section === "business" && (
        <>
          <div className="grid">
            <Stat label="Tenants" value={num(b.tenants)} sub={`${b.signups_period} signups in ${a.days}d`} />
            <Stat label="Trialing / active" value={`${b.trialing} / ${b.active}`} sub={`${b.past_due} past due · ${b.paused} paused · ${b.suspended} suspended · ${b.cancelled} cancelled`} />
            <Stat label="Trial conversion" value={pct(b.trial_conversion_pct)} sub={`${b.conversions_period} converted · ${b.churned_period} churned`} />
            <Stat label="MRR" value={gbp(b.mrr_pence)} sub={`ARR ${gbp(b.arr_pence)}`} />
            <Stat label="Overage revenue" value={gbp(b.overage_pence_period)} sub={`credits outstanding ${gbp(b.credit_outstanding_pence)}`} />
          </div>
          <div className="grid" style={{ gridTemplateColumns: "2fr 1fr" }}>
            <div className="card"><h2>Signups per day</h2><Bars points={b.signups_by_day} tall /></div>
            <div className="card"><h2>Plan mix</h2><Mix data={b.plan_mix} /></div>
          </div>
          <div className="grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
            <div className="card">
              <h2>Top accounts</h2>
              <table>
                <thead><tr><th>Tenant</th><th>Plan</th><th>Status</th><th>Minutes</th><th>Est. bill</th></tr></thead>
                <tbody>
                  {b.top_accounts.map((t) => (
                    <tr key={String(t.tenant_id)}>
                      <td><TenantLink id={String(t.tenant_id)} /></td>
                      <td>{String(t.plan_id)}</td>
                      <td>{String(t.status)}</td>
                      <td>{num(Number(t.minutes), 0)}</td>
                      <td>{gbp(Number(t.estimated_total_pence))}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="card">
              <h2>Cohort retention</h2>
              <table>
                <thead><tr><th>Signup month</th><th>Signups</th><th>Still serving</th><th>Retention</th></tr></thead>
                <tbody>
                  {b.cohorts.map((c) => (
                    <tr key={String(c.cohort)}>
                      <td>{String(c.cohort)}</td><td>{String(c.signups)}</td><td>{String(c.retained)}</td>
                      <td>{pct(typeof c.retention_pct === "number" ? c.retention_pct : null)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}

      {section === "demand" && (
        <>
          <div className="grid">
            <Stat label="Calls" value={num(d.calls)} sub={d.growth_pct == null ? "no prior period" : `${d.growth_pct >= 0 ? "+" : ""}${d.growth_pct.toFixed(1)}% vs previous ${a.days}d`} />
            <Stat label="Minutes" value={num(d.minutes, 0)} />
            <Stat label="Inbound / outbound" value={`${d.inbound} / ${d.outbound}`} />
            <Stat label="Answered" value={num(d.answered)} sub={`${d.missed} missed · ${d.failed} failed`} />
            <Stat label="Transferred / ticketed" value={`${d.transferred} / ${d.ticketed}`} />
            <Stat label="Peak concurrency" value={`${d.peak_concurrency} / ${d.capacity_concurrent}`} sub="peak vs sold capacity" />
            <Stat label="Forecast next period" value={num(d.forecast_calls_next_period)} sub="from last 7 days' run-rate" />
          </div>
          <div className="grid" style={{ gridTemplateColumns: "2fr 1fr" }}>
            <div className="card"><h2>Calls per day</h2><Bars points={d.calls_by_day} tall /></div>
            <div className="card"><h2>Channel mix</h2><Mix data={d.channel_mix} /></div>
          </div>
          <div className="card"><h2>Calls by hour (UK)</h2><Bars points={d.calls_by_hour} /></div>
        </>
      )}

      {section === "quality" && (
        <>
          <div className="grid">
            <Stat label="Answer latency" value={secs(q.answer_latency_p50_s)} sub={`p95 ${secs(q.answer_latency_p95_s)}`} />
            <Stat label="Turn latency" value={millis(q.turn_latency_p50_ms)} sub={`p95 ${millis(q.turn_latency_p95_ms)}`} />
            <Stat label="Vendor spend" value={gbp(Math.round(q.vendor_cost_pence))} sub={`cost/call ${q.cost_per_call_pence == null ? "—" : gbp(Math.round(q.cost_per_call_pence))}`} />
            <Stat label="Gross margin" value={pct(q.gross_margin_pct)} sub={`revenue ${gbp(q.revenue_pence)}`} />
            <Stat label="QA average" value={q.qa_overall_avg == null ? "—" : q.qa_overall_avg.toFixed(1)} sub={`${q.low_score_calls} low-score calls`} />
            <Stat label="Connector failures" value={pct(q.connector_failure_pct)} sub={`${q.connector_failed} of ${q.connector_jobs} jobs`} />
          </div>
          <div className="grid" style={{ gridTemplateColumns: "2fr 1fr" }}>
            <div className="card"><h2>QA score per day</h2><Bars points={q.qa_by_day} tall fmt={(v) => v.toFixed(1)} /></div>
            <div className="card">
              <h2>Connector usage</h2><Mix data={q.connectors_by_provider} />
              <h2 style={{ marginTop: "1rem" }}>Tenant health</h2><Mix data={q.tenant_health} />
            </div>
          </div>
        </>
      )}
    </>
  );
}
