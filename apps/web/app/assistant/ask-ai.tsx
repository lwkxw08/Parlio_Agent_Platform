"use client";

import { useState } from "react";
import { type DraftField, requestDraft } from "@/lib/api";

type Props = {
  assistantId: string;
  field: DraftField;
  current: string;
  onInsert: (text: string) => void;
  website?: string | null;
  context?: string | null;
  placeholder?: string;
};

/** "Ask AI to draft" — a brief in, optimised field wording out; the user reviews before inserting. */
export default function AskAi({ assistantId, field, current, onInsert, website, context, placeholder }: Props) {
  const [open, setOpen] = useState(false);
  const [brief, setBrief] = useState("");
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState<string | null>(null);
  const [notes, setNotes] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    if (brief.trim().length < 3) return;
    setBusy(true); setError(null); setDraft(null);
    const r = await requestDraft(assistantId, { field, brief, current, website: website ?? null, context: context ?? null });
    setBusy(false);
    if (!r.ok) return setError(r.error);
    setDraft(r.data.text);
    setNotes([...(r.data.website_used ? [`Used ${r.data.website_used}`] : []), ...r.data.notes, ...(r.data.source === "template" ? ["Drafted without an AI model (no OpenAI key configured) — edit as needed."] : [])]);
  };
  const insert = (mode: "replace" | "append") => {
    if (draft == null) return;
    onInsert(mode === "replace" || !current.trim() ? draft : `${current.trimEnd()}\n${draft}`);
    setOpen(false); setDraft(null); setBrief("");
  };

  if (!open) {
    return <button type="button" className="ghost small" style={{ marginLeft: "0.5rem", padding: "0.1rem 0.5rem" }} onClick={() => setOpen(true)} title="Describe what you want and AI drafts the wording">✨ Ask AI to draft</button>;
  }
  return (
    <div className="card" style={{ margin: "0.4rem 0 0.6rem", padding: "0.75rem", background: "var(--bg)", border: "1px solid var(--line)" }}>
      <div className="row" style={{ gap: "0.5rem", alignItems: "flex-start" }}>
        <textarea
          value={brief}
          placeholder={placeholder ?? "e.g. Help me write this for Parlio Demo Plumbing — use details from www.parliodemo.co.uk, mention 24/7 emergency call-outs"}
          onChange={(e) => setBrief(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) run(); }}
          style={{ flex: 1, minHeight: "3.2rem" }}
        />
        <button type="button" className="primary" disabled={busy || brief.trim().length < 3} onClick={run}>{busy ? "Drafting…" : "Draft"}</button>
        <button type="button" className="ghost" onClick={() => { setOpen(false); setDraft(null); }}>Cancel</button>
      </div>
      <p className="hint small" style={{ margin: "0.3rem 0 0" }}>Include a website address and AI will pull facts from it. Nothing is saved until you insert and save a new version.</p>
      {error && <p className="small" style={{ color: "var(--bad-fg)" }}>{error}</p>}
      {draft != null && (
        <div style={{ marginTop: "0.6rem" }}>
          <textarea value={draft} onChange={(e) => setDraft(e.target.value)} style={{ width: "100%", minHeight: "6rem" }} />
          {notes.length > 0 && <p className="muted small" style={{ margin: "0.2rem 0" }}>{notes.join(" · ")}</p>}
          <div className="row" style={{ gap: "0.5rem", marginTop: "0.3rem" }}>
            <button type="button" className="primary" onClick={() => insert("replace")}>{current.trim() ? "Replace field" : "Insert"}</button>
            {current.trim() && <button type="button" className="ghost" onClick={() => insert("append")}>Append to existing</button>}
            <button type="button" className="ghost" onClick={run} disabled={busy}>Try again</button>
          </div>
        </div>
      )}
    </div>
  );
}
