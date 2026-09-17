import { fetchShared, secs, when } from "@/lib/api";
import { Transcript } from "../../transcript";

export const dynamic = "force-dynamic";

export default async function Shared({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  const c = await fetchShared(token);
  if (!c) {
    return <div className="share section"><h2>Link not found</h2><p className="muted">This summary link is invalid or has been revoked.</p></div>;
  }
  return (
    <div className="share">
      <div className="section">
        <h2>Call summary{c.business_name ? ` — ${c.business_name}` : ""}</h2>
        <p className="muted small">{when(c.started_at)} · {secs(c.duration_s)} · caller {c.caller ?? "withheld"}</p>
        <p>{c.summary ?? <span className="muted">No summary available.</span>}</p>
        {Object.keys(c.extracted).length > 0 && (
          <dl className="kv">
            {Object.entries(c.extracted).map(([k, v]) => (
              <div key={k} style={{ display: "contents" }}><dt>{k.replaceAll("_", " ")}</dt><dd>{String(v)}</dd></div>
            ))}
          </dl>
        )}
      </div>
      <div className="section">
        <h2>Transcript</h2>
        <Transcript turns={c.transcript} startedAt={c.started_at} />
      </div>
      <p className="muted small" style={{ textAlign: "center" }}>Shared via ParlioTec</p>
    </div>
  );
}
