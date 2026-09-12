import { fetchAssistants, fetchCalls, fetchHealth, ms } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function Overview() {
  const [health, calls, assistants] = await Promise.all([
    fetchHealth(),
    fetchCalls(),
    fetchAssistants(),
  ]);
  const list = calls ?? [];
  const answered = list.filter((c) => c.answered_at);
  const avg = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);
  const answerLatency = avg(answered.map((c) => c.answer_latency_s ?? 0));
  const p50 = avg(list.map((c) => c.latency.p50_s).filter((x): x is number => x != null));

  return (
    <>
      <h1>Overview</h1>
      <div className="grid">
        <div className="card">
          <div className="label">Core API</div>
          <div className="value">
            <span className={`pill ${health ? "ok" : "bad"}`}>
              {health ? `${health.status} · ${health.env}` : "unreachable"}
            </span>
          </div>
        </div>
        <div className="card"><div className="label">Calls</div><div className="value">{list.length}</div></div>
        <div className="card"><div className="label">Answered</div><div className="value">{answered.length}</div></div>
        <div className="card"><div className="label">Avg pick-up</div><div className="value">{ms(answerLatency)}</div></div>
        <div className="card"><div className="label">Avg turn p50</div><div className="value">{ms(p50)}</div></div>
      </div>

      <h1>Assistants</h1>
      <table>
        <thead><tr><th>Name</th><th>Business</th><th>Language</th><th>Region profile</th><th>Greeting</th></tr></thead>
        <tbody>
          {(assistants ?? []).map((a) => (
            <tr key={a.assistant_id}>
              <td>{a.name}</td><td>{a.business_name}</td><td>{a.language}</td>
              <td><span className="pill">{a.region_profile}</span></td>
              <td className="muted">{a.greeting}</td>
            </tr>
          ))}
          {!assistants?.length && <tr><td colSpan={5} className="muted">No assistants (API offline?)</td></tr>}
        </tbody>
      </table>
    </>
  );
}
