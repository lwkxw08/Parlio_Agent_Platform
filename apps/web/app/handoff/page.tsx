import Link from "next/link";
import { fetchAssistants, fetchHandoffAnalytics, fetchTransfers, secs } from "@/lib/api";
import Destinations from "./destinations";

export const dynamic = "force-dynamic";

function Breakdown({ title, data }: { title: string; data: Record<string, number> }) {
  const rows = Object.entries(data).sort((a, b) => b[1] - a[1]);
  return (
    <div className="card">
      <div className="label">{title}</div>
      {rows.length ? rows.map(([k, v]) => (
        <div key={k} className="row between"><span>{k}</span><strong>{v}</strong></div>
      )) : <div className="muted small">No data</div>}
    </div>
  );
}

export default async function Handoff() {
  const [stats, transfers, assistants] = await Promise.all([fetchHandoffAnalytics(), fetchTransfers(), fetchAssistants()]);
  const assistant = assistants?.[0];
  const tr = stats?.transfers;
  const tk = stats?.tickets;
  const pct = (x: number | null | undefined) => (x == null ? "—" : `${Math.round(x * 100)}%`);

  return (
    <>
      <h1>Transfers &amp; tickets</h1>
      {assistant ? <Destinations assistant={assistant} /> : <p className="muted">No assistant yet — <Link href="/onboarding">run the setup wizard</Link> to add departments.</p>}
      <div className="grid">
        <div className="card"><div className="label">Transfers</div><div className="value">{tr?.total ?? "—"}</div></div>
        <div className="card"><div className="label">Human answer rate</div><div className="value">{pct(tr?.answer_rate)}</div></div>
        <div className="card"><div className="label">Tickets open / claimed</div><div className="value">{tk ? `${tk.open} / ${tk.claimed}` : "—"}</div></div>
        <div className="card"><div className="label">SLA breached</div><div className="value">{tk?.sla_breached ?? "—"}</div></div>
        <div className="card"><div className="label">Avg time to claim</div><div className="value">{secs(tk?.avg_time_to_claim_s)}</div></div>
        <div className="card"><div className="label">Avg time to resolve</div><div className="value">{secs(tk?.avg_time_to_resolve_s)}</div></div>
      </div>
      <div className="grid">
        <Breakdown title="Transfers by outcome" data={tr?.by_outcome ?? {}} />
        <Breakdown title="Transfers by department" data={tr?.by_department ?? {}} />
        <Breakdown title="Transfers by destination" data={tr?.by_destination ?? {}} />
        <Breakdown title="Tickets by priority" data={tk?.by_priority ?? {}} />
        <Breakdown title="Tickets by category" data={tk?.by_category ?? {}} />
      </div>
      <h1>Recent transfers</h1>
      <table>
        <thead><tr><th>Started</th><th>Call</th><th>Destination</th><th>Department</th><th>Mode</th><th>Outcome</th><th>Ring time</th></tr></thead>
        <tbody>
          {(transfers ?? []).map((t) => (
            <tr key={t.id}>
              <td>{new Date(t.started_at).toLocaleString("en-GB")}</td>
              <td><Link href={`/calls/${t.call_id}`}>{t.call_id}</Link></td>
              <td>{t.destination}</td>
              <td>{t.department ?? "—"}</td>
              <td>{t.mode}</td>
              <td><span className={`pill ${t.outcome === "answered" ? "ok" : t.outcome === "no_answer" || t.outcome === "rejected" ? "bad" : ""}`}>{t.outcome}</span></td>
              <td>{t.ended_at ? secs((new Date(t.ended_at).getTime() - new Date(t.started_at).getTime()) / 1000) : "—"}</td>
            </tr>
          ))}
          {!transfers?.length && <tr><td colSpan={7} className="muted">No transfers yet</td></tr>}
        </tbody>
      </table>
    </>
  );
}
