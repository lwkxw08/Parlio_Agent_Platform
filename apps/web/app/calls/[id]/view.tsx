"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { type CallRecord, ms, post, secs, when } from "@/lib/api";

const TABS = ["overview", "recording", "transfers", "transcript", "all"] as const;
type Tab = (typeof TABS)[number];

const FEEDBACK = [
  ["incorrect", "Incorrect response"],
  ["tone", "Wrong tone"],
  ["missed_transfer", "Should have transferred"],
  ["latency", "Too slow"],
  ["other", "Other"],
] as const;

export default function CallView({ initial }: { initial: CallRecord }) {
  const [call, setCall] = useState(initial);
  const [tab, setTab] = useState<Tab>("overview");
  const [fbType, setFbType] = useState<string>("incorrect");
  const [fbNote, setFbNote] = useState("");
  const [toast, setToast] = useState<string | null>(null);
  const [shareUrl, setShareUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!call.read) post<CallRecord>(`/v1/calls/${call.call_id}/read`, { read: true }).then((c) => c && setCall(c));
  }, [call.call_id, call.read]);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 2500);
    return () => clearTimeout(t);
  }, [toast]);

  const share = async () => {
    const r = await post<{ token: string; url: string }>(`/v1/calls/${call.call_id}/share`);
    if (!r) return setToast("Could not create link");
    const url = `${window.location.origin}/share/${r.token}`;
    setShareUrl(url);
    await navigator.clipboard?.writeText(url).catch(() => undefined);
    setToast("Share link copied");
  };

  const sendFeedback = async () => {
    const c = await post<CallRecord>(`/v1/calls/${call.call_id}/feedback`, { type: fbType, note: fbNote || null });
    if (c) {
      setCall(c);
      setFbNote("");
      setToast("Thanks — feedback recorded");
    }
  };

  const toggleRead = async () => {
    const c = await post<CallRecord>(`/v1/calls/${call.call_id}/read`, { read: !call.read });
    if (c) setCall(c);
  };

  const showOverview = tab === "overview" || tab === "all";
  const showRecording = tab === "recording" || tab === "all";
  const showTransfers = tab === "transfers" || tab === "all";
  const showTranscript = tab === "transcript" || tab === "all";

  return (
    <>
      <p className="small"><Link href="/calls">← Calls</Link></p>
      <div style={{ display: "flex", alignItems: "center", gap: "0.8rem", flexWrap: "wrap" }}>
        <h1 style={{ margin: 0 }}>{call.caller ?? "Unknown caller"}</h1>
        <span className={`pill ${call.kind === "missed" || call.kind === "blocked" ? "bad" : call.kind === "answered" ? "ok" : ""}`}>{call.kind}</span>
        {call.escalated && <span className="pill urgent">urgent: {call.escalation_keyword}</span>}
        {call.caller_type && <span className="pill">{call.caller_type} caller</span>}
        <span style={{ marginLeft: "auto", display: "flex", gap: "0.4rem" }}>
          <button className="ghost" onClick={toggleRead}>{call.read ? "Mark unread" : "Mark read"}</button>
          <button className="ghost" onClick={share}>Share summary</button>
          {call.contact_id && <Link className="btn" href={`/contacts/${call.contact_id}`}>Contact</Link>}
        </span>
      </div>
      <p className="muted small">{when(call.started_at)} · {call.dialed ?? "—"} · {call.call_id}</p>
      {shareUrl && <p className="small">Public link: <code>{shareUrl}</code></p>}

      <div className="tabs">
        {TABS.map((t) => (
          <button key={t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>
            {t === "all" ? "Everything" : t[0].toUpperCase() + t.slice(1)}
          </button>
        ))}
      </div>

      {showOverview && (
        <>
          <div className="grid">
            <div className="card"><div className="label">Pick-up</div><div className="value">{ms(call.answer_latency_s)}</div></div>
            <div className="card"><div className="label">Turn latency p50 / p95</div><div className="value">{ms(call.latency.p50_s)} <span className="muted small">/ {ms(call.latency.p95_s)}</span></div></div>
            <div className="card"><div className="label">Duration</div><div className="value">{secs(call.duration_s)}</div></div>
            <div className="card"><div className="label">Turns</div><div className="value">{call.latency.turns ?? "—"}</div></div>
          </div>
          <div className="section">
            <h2>Summary</h2>
            <p>{call.summary ?? <span className="muted">No summary yet.</span>}</p>
            {Object.keys(call.extracted).length > 0 && (
              <dl className="kv">
                {Object.entries(call.extracted).map(([k, v]) => (
                  <div key={k} style={{ display: "contents" }}><dt>{k.replaceAll("_", " ")}</dt><dd>{String(v)}</dd></div>
                ))}
              </dl>
            )}
            {call.missed_fields.length > 0 && (
              <p className="small muted">Not captured: {call.missed_fields.join(", ")}</p>
            )}
            {call.ticket_ids.length > 0 && (
              <p className="small">Tickets: {call.ticket_ids.map((t) => <Link key={t} href={`/tickets/${t}`} style={{ marginRight: 6 }}>{t.slice(0, 8)}</Link>)}</p>
            )}
            {call.end_reason && <p className="small muted">Ended: {call.end_reason}</p>}
          </div>
          <div className="section">
            <h2>Was something wrong?</h2>
            <p className="hint">Flag issues so the assistant can be tuned. Feedback is reviewed alongside the transcript.</p>
            <div className="filters">
              <select value={fbType} onChange={(e) => setFbType(e.target.value)}>
                {FEEDBACK.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
              <input placeholder="Optional note" value={fbNote} onChange={(e) => setFbNote(e.target.value)} style={{ flex: 1, minWidth: 220 }} />
              <button className="ghost" onClick={sendFeedback}>Send feedback</button>
            </div>
            {call.feedback.length > 0 && (
              <ul className="small">
                {call.feedback.map((f, i) => <li key={i}><b>{f.type}</b>{f.note ? ` — ${f.note}` : ""} <span className="muted">({when(f.at)})</span></li>)}
              </ul>
            )}
          </div>
        </>
      )}

      {showRecording && (
        <div className="section">
          <h2>AI recording</h2>
          {call.recordings.length ? (
            <ul>
              {call.recordings.map((k) => (
                <li key={k}><code>{k}</code></li>
              ))}
            </ul>
          ) : (
            <p className="muted">No recording for this call{call.kind === "blocked" ? " (blocked before answer)" : ""}.</p>
          )}
          <p className="hint">Playback and download links are served from object storage once the media service is deployed.</p>
        </div>
      )}

      {showTransfers && (
        <div className="section">
          <h2>Transfers</h2>
          {call.transfers.length ? (
            <table>
              <thead><tr><th>At</th><th>Destination</th><th>Department</th><th>Mode</th><th>Outcome</th></tr></thead>
              <tbody>
                {call.transfers.map((t) => (
                  <tr key={t.transfer_id}>
                    <td>{when(t.at)}</td><td>{t.destination}</td><td>{t.department ?? "—"}</td><td>{t.mode}</td>
                    <td><span className={`pill ${t.outcome === "answered" || t.outcome === "bridged" ? "ok" : "bad"}`}>{t.outcome}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <p className="muted">No transfer on this call.</p>}
        </div>
      )}

      {showTranscript && (
        <div className="section">
          <h2>Transcript</h2>
          <div className="transcript">
            {call.transcript.map((t, i) => (
              <div key={i} className={`bubble ${t.role}`}>
                <span className="who">{t.role === "assistant" ? call.assistant_id : "Caller"}{t.interrupted ? " (interrupted)" : ""}</span>
                {t.text}
              </div>
            ))}
            {!call.transcript.length && <p className="muted">No transcript.</p>}
          </div>
        </div>
      )}

      {toast && <div className="toast">{toast}</div>}
    </>
  );
}
