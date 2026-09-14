"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { type PlatformStatus, type PlatformStatusLevel, put, when } from "@/lib/api";

const LEVELS: [PlatformStatusLevel, string][] = [
  ["ok", "All systems operational (no banner)"],
  ["degraded", "Degraded performance"],
  ["incident", "Incident in progress"],
  ["maintenance", "Scheduled maintenance"],
];

const toLocal = (iso: string | null) => (iso ? new Date(iso).toISOString().slice(0, 16) : "");
const fromLocal = (v: string) => (v ? new Date(v).toISOString() : null);

export default function Status({ status: initial, canEdit }: { status: PlatformStatus; canEdit: boolean }) {
  const [s, setS] = useState<PlatformStatus>(initial);
  const [msg, setMsg] = useState<string | null>(null);
  const router = useRouter();
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    const r = await put<PlatformStatus>("/v1/admin/status", s);
    if (!r.ok) return setMsg(r.error);
    setS(r.data);
    setMsg(r.data.level === "ok" ? "Banner cleared" : "Banner published to every tenant dashboard");
    router.refresh();
  };
  const clear = () => setS({ ...s, level: "ok", title: "", message: "", link: null, starts_at: null, ends_at: null });
  return (
    <div className="grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
      <form className="section form" onSubmit={save}>
        <h2>Platform status banner</h2>
        <p className="hint">Shown at the top of every tenant dashboard (and on <code>/v1/public/status</code>) while active. Use it for incidents and planned maintenance.</p>
        <fieldset disabled={!canEdit} style={{ border: 0, padding: 0, margin: 0, display: "contents" }}>
          <label>Level
            <select value={s.level} onChange={(e) => setS({ ...s, level: e.target.value as PlatformStatusLevel })}>
              {LEVELS.map(([l, label]) => <option key={l} value={l}>{label}</option>)}
            </select>
          </label>
          <label>Title<input value={s.title} onChange={(e) => setS({ ...s, title: e.target.value })} maxLength={120} placeholder="e.g. Delayed call summaries" /></label>
          <label>Message<textarea value={s.message} onChange={(e) => setS({ ...s, message: e.target.value })} rows={3} maxLength={1000} placeholder="What's affected, what we're doing, when to expect an update" /></label>
          <label>Link (optional)<input type="url" value={s.link ?? ""} onChange={(e) => setS({ ...s, link: e.target.value || null })} placeholder="https://status.parlio.co.uk/..." /></label>
          <div className="two">
            <label>Show from<input type="datetime-local" value={toLocal(s.starts_at)} onChange={(e) => setS({ ...s, starts_at: fromLocal(e.target.value) })} /></label>
            <label>Hide after<input type="datetime-local" value={toLocal(s.ends_at)} onChange={(e) => setS({ ...s, ends_at: fromLocal(e.target.value) })} /></label>
          </div>
          {canEdit && <div className="row"><button type="submit" className="primary">Publish</button><button type="button" className="ghost" onClick={clear}>Clear banner</button></div>}
        </fieldset>
        {msg && <p className="small" style={{ color: "var(--accent)" }}>{msg}</p>}
        {s.updated_by && <p className="muted small">Last changed by {s.updated_by} · {when(s.updated_at)}</p>}
      </form>
      <div className="section">
        <h2>Preview</h2>
        {s.level === "ok" ? <p className="muted small">No banner will be shown.</p> : (
          <div className={`banner ${s.level === "incident" ? "bad" : "warn"}`}>
            <strong>{s.title || LEVELS.find(([l]) => l === s.level)?.[1]}</strong>
            <span>{s.message}</span>
            {s.link && <a href={s.link} target="_blank" rel="noreferrer">Details</a>}
          </div>
        )}
        <h2 style={{ marginTop: "1rem" }}>Live health</h2>
        <p className="small muted">Service health, latency and vendor status panels arrive with Phase 17 (monitoring). Until then use the Overview latency/margin cards and the API <code>/metrics</code> endpoint.</p>
      </div>
    </div>
  );
}
