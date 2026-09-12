import { notFound } from "next/navigation";
import { fetchCall, ms } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function CallDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const call = await fetchCall(id);
  if (!call) notFound();

  return (
    <>
      <h1>Call {call.call_id}</h1>
      <div className="grid">
        <div className="card"><div className="label">Caller</div><div className="value">{call.caller ?? "—"}</div></div>
        <div className="card"><div className="label">Status</div><div className="value">{call.status}</div></div>
        <div className="card"><div className="label">Pick-up</div><div className="value">{ms(call.answer_latency_s)}</div></div>
        <div className="card"><div className="label">Turn p50 / p95</div><div className="value">{ms(call.latency.p50_s)} / {ms(call.latency.p95_s)}</div></div>
      </div>
      {call.recordings.length > 0 && (
        <p className="muted">Recordings: {call.recordings.join(", ")}</p>
      )}
      <h1>Transcript</h1>
      <div className="transcript">
        {call.transcript.map((m, i) => (
          <div key={i} className={`msg ${m.role}`}>
            {m.text}{m.interrupted ? <span className="muted"> (interrupted)</span> : null}
          </div>
        ))}
        {!call.transcript.length && <p className="muted">No transcript captured.</p>}
      </div>
    </>
  );
}
