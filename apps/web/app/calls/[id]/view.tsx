"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { type CallExplanation, type CallRecord, type Scenario, callParty, fetchExplanation, ms, phone, post, request, secs, when } from "@/lib/api";
import { Transcript } from "../../transcript";
import { RecordingLeg } from "./recording";
import { humanize } from "@/app/breakdown";

const TABS = ["overview", "recording", "transfers", "transcript", "why", "all"] as const;
type Tab = (typeof TABS)[number];

const FEEDBACK = [
  ["incorrect", "Incorrect response"],
  ["tone", "Wrong tone"],
  ["missed_transfer", "Should have transferred"],
  ["latency", "Too slow"],
  ["other", "Other"],
] as const;

export type Seek = { index: number; offset_s: number };

const isTab = (t: string | undefined): t is Tab => (TABS as readonly string[]).includes(t ?? "");

export default function CallView({ initial, initialTab, seek = null }: { initial: CallRecord; initialTab?: string; seek?: Seek | null }) {
  const [call, setCall] = useState(initial);
  const [tab, setTab] = useState<Tab>(isTab(initialTab) ? initialTab : "overview");
  const [fbType, setFbType] = useState<string>("incorrect");
  const [fbNote, setFbNote] = useState("");
  const [toast, setToast] = useState<string | null>(null);
  const [shareUrl, setShareUrl] = useState<string | null>(null);
  const [why, setWhy] = useState<CallExplanation | null>(null);
  const [whyError, setWhyError] = useState<string | null>(null);
  const [blocked, setBlocked] = useState<boolean>(call.kind === "blocked");
  const [blocking, setBlocking] = useState(false);

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

  const keepAsTest = async () => {
    const r = await request<Scenario>(`/v1/quality/regression/from-call/${call.call_id}?tenant_id=${call.tenant_id}`, { method: "POST" });
    setToast(r.ok ? `Saved as regression test “${r.data.name}” — it now runs on every Studio save` : `Could not save: ${r.error}`);
  };

  const sendFeedback = async () => {
    const c = await post<CallRecord>(`/v1/calls/${call.call_id}/feedback`, { type: fbType, note: fbNote || null });
    if (c) {
      setCall(c);
      setFbNote("");
      setToast("Thanks — feedback recorded");
    }
  };

  const toggleBlock = async () => {
    if (!call.party) return;
    const next = !blocked;
    if (next && !confirm(`Block ${phone(call.party)}? Future calls from this number will be rejected before the assistant answers.`)) return;
    setBlocking(true);
    const r = await request<{ number: string; blocked: boolean }>(`/v1/calls/${call.call_id}/block?block=${next}`, { method: "POST" });
    setBlocking(false);
    if (!r.ok) return setToast(`Could not ${next ? "block" : "unblock"}: ${r.error}`);
    setBlocked(r.data.blocked);
    setToast(r.data.blocked ? `${phone(r.data.number)} blocked — applies from the next call` : `${phone(r.data.number)} unblocked`);
  };

  const toggleRead = async () => {
    const c = await post<CallRecord>(`/v1/calls/${call.call_id}/read`, { read: !call.read });
    if (c) setCall(c);
  };

  const showOverview = tab === "overview" || tab === "all";
  const showRecording = tab === "recording" || tab === "all";
  const showTransfers = tab === "transfers" || tab === "all";
  const showTranscript = tab === "transcript" || tab === "all";
  const showWhy = tab === "why" || tab === "all";

  useEffect(() => {
    if (!showWhy || why || whyError) return;
    fetchExplanation(call.call_id).then((r) => (r.ok ? setWhy(r.data) : setWhyError(r.error)));
  }, [showWhy, why, whyError, call.call_id]);

  return (
    <>
      <p className="small"><Link href="/calls">← Calls</Link></p>
      <div style={{ display: "flex", alignItems: "center", gap: "0.8rem", flexWrap: "wrap" }}>
        <h1 style={{ margin: 0 }}>{call.party ? callParty(call) : call.direction === "outbound" ? "Outbound call" : "Unknown caller"}</h1>
        {call.direction === "outbound" && <span className="pill accent">Outbound</span>}
        <span className={`pill ${call.kind === "missed" || call.kind === "blocked" ? "bad" : call.kind === "answered" ? "ok" : ""}`}>{humanize(call.kind)}</span>
        {call.escalated && <span className="pill urgent">urgent: {call.escalation_keyword}</span>}
        {call.caller_type && <span className="pill">{call.caller_type} caller</span>}
        <span style={{ marginLeft: "auto", display: "flex", gap: "0.4rem" }}>
          <button className="ghost" onClick={toggleRead}>{call.read ? "Mark unread" : "Mark read"}</button>
          <button className="ghost" onClick={share}>Share summary</button>
          {call.party && call.direction !== "outbound" && (
            <button className={blocked ? "ghost" : "danger"} disabled={blocking} title="Adds this number to the assistant's blocked list (Studio → Blocked numbers)" onClick={toggleBlock}>
              {blocked ? "Unblock caller" : "Block caller"}
            </button>
          )}
          <button className="ghost" title="Replay this caller's words against every future Studio change" onClick={keepAsTest}>Save as regression test</button>
          {call.contact_id && <Link className="btn" href={`/contacts/${call.contact_id}`}>Contact</Link>}
        </span>
      </div>
      <p className="muted small">
        {when(call.started_at)} ·{" "}
        {call.direction === "outbound"
          ? `the assistant rang ${phone(call.party)}${call.caller && call.caller !== "unknown" ? ` from ${phone(call.caller)}` : ""}`
          : `${phone(call.party)} rang ${phone(call.dialed)}`}{" "}
        · {call.call_id}
      </p>
      {shareUrl && <p className="small">Public link: <code>{shareUrl}</code></p>}

      <div className="tabs">
        {TABS.map((t) => (
          <button key={t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>
            {t === "all" ? "Everything" : t === "why" ? "Why did it say that?" : t[0].toUpperCase() + t.slice(1)}
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
            {call.ticket_ids.length > 0 && (
              <p className="small">Tickets: {call.ticket_ids.map((t) => <Link key={t} href={`/tickets/${t}`} style={{ marginRight: 6 }}>{t.slice(0, 8)}</Link>)}</p>
            )}
            {call.end_reason && <p className="small muted">Ended: {call.end_reason}</p>}
          </div>
          <ExtractedDetails extracted={call.extracted} missed={call.missed_fields} />
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
          <h2>Recording</h2>
          {call.recordings.length ? (
            <div className="recording-legs">
              {call.recordings.map((k, i) => (
                <RecordingLeg key={k} callId={call.call_id} index={i} objectKey={k} seekTo={seek && seek.index === i ? seek.offset_s : null} />
              ))}
            </div>
          ) : (
            <p className="muted">No recording for this call{call.kind === "blocked" ? " (blocked before answer)" : ""}.</p>
          )}
          {call.recordings.length > 0 && <p className="hint">Each side of the call is recorded separately{call.transfers.some((t) => t.recorded) ? ", including the team member who took the transfer" : ""}. Recordings are kept per your retention policy.</p>}
        </div>
      )}

      {showTransfers && (
        <div className="section">
          <h2>Transfers</h2>
          {call.transfers.length ? (
            <table>
              <thead><tr><th>When</th><th>Destination</th><th>Department</th><th>Mode</th><th>Outcome</th><th>Human talk time</th></tr></thead>
              <tbody>
                {call.transfers.map((t) => (
                  <tr key={t.transfer_id}>
                    <td>{t.at ? when(t.at) : "—"}</td><td>{t.destination}</td><td>{t.department ?? "—"}</td><td>{humanize(t.mode)}</td>
                    <td><span className={`pill ${t.outcome === "answered" || t.outcome === "bridged" ? "ok" : "bad"}`}>{humanize(t.outcome)}</span></td>
                    <td>{t.human_duration_s != null ? secs(t.human_duration_s) : "—"}{t.recorded ? <span className="pill" style={{ marginLeft: 6 }}>Recorded</span> : null}</td>
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
          <Transcript turns={call.transcript} startedAt={call.answered_at ?? call.started_at} />
        </div>
      )}

      {showWhy && (
        <div className="section">
          <h2>Why did the AI say this?</h2>
          {whyError && <p className="muted small">{whyError}</p>}
          {!why && !whyError && <p className="muted small">Matching each answer to your assistant configuration…</p>}
          {why && (
            <>
              <p className="hint">{why.note}{why.assistant_version != null && ` Configuration: ${why.assistant_id} v${why.assistant_version}.`}</p>
              {!why.turns.length && <p className="muted">No assistant turns in this call.</p>}
              {why.turns.map((t) => (
                <div key={t.index} className="card" style={{ marginBottom: "0.8rem" }}>
                  <div className="bubble assistant" style={{ marginBottom: "0.5rem" }}>{t.text}</div>
                  {t.evidence.map((e, i) => (
                    <div key={i} className="small" style={{ display: "flex", gap: 8, alignItems: "baseline", margin: "0.2rem 0" }}>
                      <span className={`pill ${e.kind === "none" ? "" : e.score >= 0.5 ? "ok" : "warn"}`}>{humanize(e.kind)}</span>
                      <span><strong>{e.label}</strong>{e.text && <> — <span className="muted">{e.text}</span></>}{e.kind !== "none" && e.kind !== "greeting" && <span className="muted"> · match {Math.round(e.score * 100)}%</span>}</span>
                    </div>
                  ))}
                </div>
              ))}
            </>
          )}
        </div>
      )}

      {toast && <div className="toast">{toast}</div>}
    </>
  );
}

const fieldValue = (v: unknown): string => {
  if (v == null || v === "") return "";
  if (Array.isArray(v)) return v.map(fieldValue).filter(Boolean).join(", ");
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
};

/** Post-call extraction: every detail the assistant captured, then the required ones it did not. */
function ExtractedDetails({ extracted, missed }: { extracted: Record<string, unknown>; missed: string[] }) {
  const captured = Object.entries(extracted)
    .map(([k, v]) => [k, fieldValue(v)] as const)
    .filter(([, v]) => v !== "");
  const missing = missed.filter((m) => !(m in extracted) || fieldValue(extracted[m]) === "");
  if (captured.length === 0 && missing.length === 0) return null;
  const total = captured.length + missing.length;
  return (
    <div className="section" id="extracted-details">
      <h2 style={{ display: "flex", alignItems: "center", gap: "0.6rem" }}>
        Extracted details
        <span className={`pill ${missing.length === 0 ? "ok" : "bad"}`}>{captured.length} of {total} captured</span>
      </h2>
      {captured.length > 0 && (
        <dl className="kv">
          {captured.map(([k, v]) => (
            <div key={k} style={{ display: "contents" }}>
              <dt>{humanize(k)}</dt>
              <dd>{v}</dd>
            </div>
          ))}
        </dl>
      )}
      {missing.length > 0 && (
        <div style={{ marginTop: captured.length ? "0.8rem" : 0 }}>
          <div className="label">Not captured</div>
          <div className="row" style={{ flexWrap: "wrap", gap: "0.4rem", marginTop: "0.3rem" }}>
            {missing.map((m) => <span key={m} className="pill bad">{humanize(m)}</span>)}
          </div>
          <p className="small muted" style={{ marginTop: "0.5rem" }}>Required fields the caller did not give. Adjust the prompt under Studio → Required fields if these are asked for too late or too rarely.</p>
        </div>
      )}
    </div>
  );
}
