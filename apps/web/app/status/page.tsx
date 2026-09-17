import { fetchPublicStatusPage, when } from "@/lib/api";
import { STATE_LABEL, stateCls } from "./state";
import { humanize } from "@/app/breakdown";

export const dynamic = "force-dynamic";

export default async function Page() {
  const s = await fetchPublicStatusPage();
  if (!s) return <><h1>ParlioTec status</h1><p className="muted">Status service unreachable</p></>;
  const open = s.incidents.filter((i) => i.status !== "resolved");
  const past = s.incidents.filter((i) => i.status === "resolved");
  return (
    <>
      <h1>ParlioTec status</h1>
      <div className={`banner ${s.overall === "operational" ? "" : stateCls(s.overall)}`} style={{ marginBottom: "1rem" }}>
        <strong>{s.overall === "operational" ? "All systems operational" : STATE_LABEL[s.overall]}</strong>
        <span>30-day voice uptime {s.uptime_30d_pct.toFixed(2)}% · updated {when(s.generated_at)} · <a href="/trust">Trust centre</a></span>
      </div>
      <div className="section">
        <h2>Components</h2>
        <table>
          <tbody>
            {s.components.map((c) => (
              <tr key={c.id}><td>{c.name}</td><td><span className={`pill ${stateCls(c.state)}`}>{STATE_LABEL[c.state]}</span></td><td className="small muted">{c.detail}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="section">
        <h2>Incidents</h2>
        {open.length === 0 && <p className="muted small">No active incidents.</p>}
        {[...open, ...past.slice(0, 10)].map((i) => (
          <div key={i.id} className="card" style={{ marginBottom: "0.8rem" }}>
            <div className="row" style={{ justifyContent: "space-between" }}>
              <strong>{i.title}</strong>
              <span><span className={`pill ${i.status === "resolved" ? "ok" : "warn"}`}>{humanize(i.status)}</span> <span className="pill">{i.severity.toUpperCase()}</span></span>
            </div>
            <div className="small muted">Started {when(i.started_at)}{i.resolved_at && ` · resolved ${when(i.resolved_at)}`} · {i.components.join(", ") || "platform"}</div>
            {i.updates.map((u, n) => <p key={n} className="small" style={{ margin: "0.3rem 0" }}><span className="muted">{when(u.at)}</span> <strong>{u.status}</strong> — {u.message}</p>)}
            {i.rca && <details className="small"><summary>Root-cause analysis</summary><p>{i.rca}</p></details>}
          </div>
        ))}
      </div>
    </>
  );
}
