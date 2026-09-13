"use client";

import { useState } from "react";
import {
  type AuditEntry,
  type ErasureResult,
  type RetentionPolicy,
  type RetentionRun,
  type RetentionView,
  type SubjectExport,
  request,
  when,
} from "@/lib/api";

const TABS = [["retention", "Retention & redaction"], ["gdpr", "Data subject requests"], ["audit", "Audit log"]] as const;
type Tab = (typeof TABS)[number][0];

export default function Compliance({ tenant, canManage, retention, audit }: { tenant: string; canManage: boolean; retention: RetentionView; audit: AuditEntry[] }) {
  const [tab, setTab] = useState<Tab>("retention");
  const [msg, setMsg] = useState<string | null>(null);
  return (
    <>
      <div className="tabs">
        {TABS.map(([id, label]) => <button key={id} className={tab === id ? "active" : ""} onClick={() => setTab(id)}>{label}</button>)}
      </div>
      {msg && <p className="small" style={{ color: "var(--accent)" }}>{msg}</p>}
      {tab === "retention" && <Retention tenant={tenant} canManage={canManage} view={retention} setMsg={setMsg} />}
      {tab === "gdpr" && <Gdpr tenant={tenant} canManage={canManage} setMsg={setMsg} />}
      {tab === "audit" && <Audit tenant={tenant} canManage={canManage} initial={audit} />}
    </>
  );
}

function Retention({ tenant, canManage, view, setMsg }: { tenant: string; canManage: boolean; view: RetentionView; setMsg: (m: string | null) => void }) {
  const [pol, setPol] = useState<RetentionPolicy>(view.policy);
  const [last, setLast] = useState<RetentionRun | null>(view.last_run);
  const q = `?tenant_id=${tenant}`;
  const set = (patch: Partial<RetentionPolicy>) => setPol((p) => ({ ...p, ...patch }));
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    const r = await request<RetentionPolicy>(`/v1/compliance/retention${q}`, {
      method: "PUT",
      body: JSON.stringify({
        transcript_days: pol.transcript_days, recording_days: pol.recording_days, call_days: pol.call_days,
        redact_on_write: pol.redact_on_write, redact_caller_number: pol.redact_caller_number,
      }),
    });
    if (!r.ok) return setMsg(`Could not save: ${r.error}`);
    setPol(r.data); setMsg("Retention policy saved.");
  };
  const run = async () => {
    const r = await request<RetentionRun>(`/v1/compliance/retention/run${q}`, { method: "POST" });
    if (!r.ok) return setMsg(`Sweep failed: ${r.error}`);
    setLast(r.data); setMsg(`Sweep done: ${r.data.transcripts_redacted} transcripts redacted, ${r.data.recordings_dropped} recordings dropped, ${r.data.calls_purged} calls purged.`);
  };
  const num = (k: "transcript_days" | "recording_days" | "call_days", label: string, hint: string) => (
    <label className="check" style={{ marginBottom: "0.8rem" }}>
      {label}
      <input type="number" min={1} max={3650} value={pol[k]} disabled={!canManage} onChange={(e) => set({ [k]: Number(e.target.value) })} style={{ maxWidth: 120 }} /> days
      <div className="muted small">{hint}</div>
    </label>
  );
  return (
    <>
      <form className="section" onSubmit={save}>
        <h2>Retention</h2>
        <p className="hint">Sweeps run hourly. Recording retention cannot exceed transcript retention; transcripts cannot outlive the call record.</p>
        {num("recording_days", "Keep recordings", "Audio is deleted after this; the transcript remains.")}
        {num("transcript_days", "Keep transcripts", "Transcript, summary and extracted fields are replaced with a redaction marker.")}
        {num("call_days", "Keep call records", "Call metadata (who/when/how long) is purged entirely — this also removes it from analytics.")}
        <h2 style={{ marginTop: "1rem" }}>PII redaction</h2>
        <p className="hint">Card numbers, emails, UK phone numbers, NI numbers, sort codes, postcodes and long spoken digit strings.</p>
        <label className="check"><input type="checkbox" checked={pol.redact_on_write} disabled={!canManage} onChange={(e) => set({ redact_on_write: e.target.checked })} /> Redact PII from transcripts as soon as the call ends</label>
        <label className="check" style={{ marginTop: "0.4rem" }}><input type="checkbox" checked={pol.redact_caller_number} disabled={!canManage || !pol.redact_on_write} onChange={(e) => set({ redact_caller_number: e.target.checked })} /> Also mask the caller&apos;s number (last six digits) — disables returning-caller recognition</label>
        {canManage && <div style={{ marginTop: "1rem", display: "flex", gap: "0.5rem" }}><button className="primary" type="submit">Save policy</button><button type="button" onClick={run}>Run sweep now</button></div>}
      </form>
      <div className="section">
        <h2>Last sweep</h2>
        {last ? (
          <dl className="kv">
            <dt>Ran</dt><dd>{when(last.ran_at)}</dd>
            <dt>Transcripts redacted</dt><dd>{last.transcripts_redacted}</dd>
            <dt>Recordings dropped</dt><dd>{last.recordings_dropped}</dd>
            <dt>Calls purged</dt><dd>{last.calls_purged}</dd>
          </dl>
        ) : <p className="muted small">No sweep has run yet for this organisation.</p>}
      </div>
    </>
  );
}

function Gdpr({ tenant, canManage, setMsg }: { tenant: string; canManage: boolean; setMsg: (m: string | null) => void }) {
  const [e164, setE164] = useState("");
  const [exp, setExp] = useState<SubjectExport | null>(null);
  const [erased, setErased] = useState<ErasureResult | null>(null);
  const q = `?tenant_id=${tenant}`;
  const doExport = async () => {
    const r = await request<SubjectExport>(`/v1/compliance/export${q}`, { method: "POST", body: JSON.stringify({ e164 }) });
    if (!r.ok) return setMsg(`Export failed: ${r.error}`);
    setExp(r.data); setErased(null);
  };
  const download = () => {
    if (!exp) return;
    const blob = new Blob([JSON.stringify(exp, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = `parlio-export-${exp.subject_e164.replace("+", "")}.json`; a.click();
    URL.revokeObjectURL(a.href);
  };
  const erase = async () => {
    if (!confirm(`Permanently erase everything held about ${e164}? Calls, transcripts and recordings are deleted; tickets are anonymised. This cannot be undone.`)) return;
    const r = await request<ErasureResult>(`/v1/compliance/erase${q}`, { method: "POST", body: JSON.stringify({ e164 }) });
    if (!r.ok) return setMsg(`Erasure failed: ${r.error}`);
    setErased(r.data); setExp(null); setMsg(`Erased: ${r.data.calls_purged} calls, ${r.data.tickets_anonymised} tickets anonymised, ${r.data.messages_deleted} messages.`);
  };
  if (!canManage) return <p className="muted">Only owners and admins can run data-subject requests.</p>;
  return (
    <>
      <div className="section">
        <h2>Subject access &amp; erasure (UK GDPR)</h2>
        <p className="hint">Enter the caller&apos;s number in international format. Export answers a subject access request (1 month deadline); erasure fulfils a right-to-be-forgotten request.</p>
        <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
          <input value={e164} onChange={(e) => setE164(e.target.value.replace(/\s+/g, ""))} placeholder="+447700900123" style={{ maxWidth: 220 }} />
          <button className="primary" onClick={doExport} disabled={!e164}>Export</button>
          <button onClick={erase} disabled={!e164} style={{ borderColor: "var(--bad-fg)", color: "var(--bad-fg)" }}>Erase</button>
        </div>
      </div>
      {exp && (
        <div className="section">
          <h2>Export for {exp.subject_e164}</h2>
          <dl className="kv">
            <dt>Generated</dt><dd>{when(exp.generated_at)}</dd>
            <dt>Contact</dt><dd>{exp.contact ? `${exp.contact.name ?? "unnamed"} (${exp.contact.call_count} calls)` : "none"}</dd>
            <dt>Calls</dt><dd>{exp.calls.length}</dd>
            <dt>Tickets</dt><dd>{exp.tickets.length}</dd>
            <dt>Messages</dt><dd>{exp.messages.length}</dd>
          </dl>
          <button style={{ marginTop: "0.8rem" }} onClick={download}>Download JSON</button>
        </div>
      )}
      {erased && (
        <div className="section">
          <h2>Erasure receipt for {erased.subject_e164}</h2>
          <dl className="kv">
            <dt>Calls purged</dt><dd>{erased.calls_purged}</dd>
            <dt>Tickets anonymised</dt><dd>{erased.tickets_anonymised}</dd>
            <dt>Messages deleted</dt><dd>{erased.messages_deleted}</dd>
            <dt>Contact deleted</dt><dd>{erased.contact_deleted ? "yes" : "no contact held"}</dd>
          </dl>
          <p className="muted small">Recorded in the audit log.</p>
        </div>
      )}
    </>
  );
}

function Audit({ tenant, canManage, initial }: { tenant: string; canManage: boolean; initial: AuditEntry[] }) {
  const [rows, setRows] = useState(initial);
  const [filter, setFilter] = useState("");
  const refresh = async () => {
    const r = await request<AuditEntry[]>(`/v1/audit?tenant_id=${tenant}&limit=500`);
    if (r.ok) setRows(r.data);
  };
  if (!canManage) return <p className="muted">Only owners and admins can view the audit log.</p>;
  const shown = rows.filter((r) => !filter || r.action.includes(filter) || r.actor.includes(filter) || (r.target ?? "").includes(filter));
  return (
    <div className="section">
      <h2>Audit log</h2>
      <p className="hint">Every change made through the dashboard or API: who, what, when and from where. Retained independently of call data.</p>
      <div style={{ display: "flex", gap: "0.5rem", marginBottom: "0.8rem" }}>
        <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter by action, actor or target" style={{ maxWidth: 320 }} />
        <button onClick={refresh}>Refresh</button>
      </div>
      {shown.length === 0 ? <p className="muted small">No entries.</p> : (
        <table>
          <thead><tr><th>When</th><th>Actor</th><th>Action</th><th>Target</th><th>Detail</th><th>IP</th></tr></thead>
          <tbody>
            {shown.map((r) => (
              <tr key={r.id}>
                <td className="small">{when(r.at)}</td><td>{r.actor}</td><td><code>{r.action}</code></td><td>{r.target ?? "—"}</td>
                <td className="small muted">{Object.keys(r.meta).length ? JSON.stringify(r.meta) : r.path}</td><td className="small muted">{r.ip ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
