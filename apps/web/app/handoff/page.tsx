import Link from "next/link";
import { fetchAssistants, fetchHandoffAnalytics, fetchTransfers, secs, when } from "@/lib/api";
import { Breakdown, humanize } from "@/app/breakdown";
import Destinations from "./destinations";

export const dynamic = "force-dynamic";

const human = humanize;

export default async function Handoff() {
  const [stats, transfers, assistants] = await Promise.all([fetchHandoffAnalytics(), fetchTransfers(), fetchAssistants()]);
  const assistant = assistants?.[0];
  const tr = stats?.transfers;
  const tk = stats?.tickets;
  const pct = (x: number | null | undefined) => (x == null ? "—" : `${Math.round(x * 100)}%`);

  return (
    <>
      <h1>Transfers &amp; tickets</h1>
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
      <h2>Transferred calls</h2>
      <p className="hint" style={{ marginTop: 0 }}>
        How your team handles calls once the assistant puts them through. Talk time needs &ldquo;Record transferred calls&rdquo; on in Assistant Studio → Recording.
      </p>
      <div className="grid">
        <div className="card"><div className="label">Recorded transfers</div><div className="value">{tr?.recorded ?? "—"}</div></div>
        <div className="card"><div className="label">Avg human talk time</div><div className="value">{secs(tr?.avg_human_duration_s)}</div></div>
        <div className="card"><div className="label">Total human talk time</div><div className="value">{tr && tr.human_talk_s > 0 ? secs(tr.human_talk_s) : "—"}</div></div>
        <Breakdown title="Human talk time by department" data={tr?.human_talk_by_department ?? {}} format={(v) => secs(v)} empty="Nothing recorded yet." />
        <Breakdown title="Human talk time by person" data={tr?.human_talk_by_destination ?? {}} format={(v) => secs(v)} empty="Nothing recorded yet." />
      </div>
      <h2>Recent transfers</h2>
      <table>
        <thead><tr><th>Started</th><th>Call</th><th>Destination</th><th>Department</th><th>Mode</th><th>Outcome</th><th>Ring time</th></tr></thead>
        <tbody>
          {(transfers ?? []).map((t) => (
            <tr key={t.id}>
              <td>{when(t.started_at)}</td>
              <td><Link href={`/calls/${t.call_id}`}>Open call</Link></td>
              <td>{t.destination}</td>
              <td>{t.department ?? "—"}</td>
              <td>{human(t.mode)}</td>
              <td><span className={`pill ${t.outcome === "answered" ? "ok" : t.outcome === "no_answer" || t.outcome === "rejected" ? "bad" : ""}`}>{human(t.outcome)}</span></td>
              <td>{t.ended_at ? secs((new Date(t.ended_at).getTime() - new Date(t.started_at).getTime()) / 1000) : "—"}</td>
            </tr>
          ))}
          {!transfers?.length && <tr><td colSpan={7} className="muted">No transfers yet</td></tr>}
        </tbody>
      </table>
      <h2>Settings</h2>
      <p className="muted small">Who calls are transferred to. Once set up you rarely need to change this.</p>
      {assistant ? <Destinations assistant={assistant} /> : <p className="muted">No assistant yet — <Link href="/onboarding">run the setup wizard</Link> to add departments.</p>}
    </>
  );
}
