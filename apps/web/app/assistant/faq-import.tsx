"use client";

import { useState } from "react";
import { type Assistant, type Faq, type FaqSource, applyFaqs, importFaqs } from "@/lib/api";

const SOURCES: [FaqSource, string, string][] = [
  ["text", "Paste text", "Paste Q&A pairs (Q: … / A: …), a numbered list, or a “Question?\\nAnswer” block."],
  ["url", "From a web page", "We read the page (e.g. your FAQs page) and pull out question/answer pairs."],
  ["csv", "CSV", "question,answer[,category] — one FAQ per row; header optional. Tabs, semicolons and pipes work too."],
];

export default function FaqImport({ assistant, onApplied }: { assistant: Assistant; onApplied: (cfg: Assistant, added: number) => void }) {
  const [source, setSource] = useState<FaqSource>("text");
  const [content, setContent] = useState("");
  const [category, setCategory] = useState("imported");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [review, setReview] = useState<{ suggested: (Faq & { keep: boolean })[]; duplicates: Faq[] } | null>(null);

  const run = async () => {
    setBusy(true); setErr(null);
    const r = await importFaqs(assistant.assistant_id, { source, content, category });
    setBusy(false);
    if (!r.ok) return setErr(r.error);
    setReview({ suggested: r.data.suggested.map((f) => ({ ...f, keep: Boolean(f.answer) })), duplicates: r.data.duplicates });
  };

  const apply = async () => {
    if (!review) return;
    const chosen = review.suggested.filter((f) => f.keep && f.question.trim() && f.answer.trim()).map(({ keep: _k, ...f }) => f);
    setBusy(true); setErr(null);
    const r = await applyFaqs(assistant.assistant_id, chosen);
    setBusy(false);
    if (!r.ok) return setErr(r.error);
    onApplied(r.data.config, r.data.added);
    setReview(null); setContent("");
  };

  const edit = (i: number, p: Partial<Faq & { keep: boolean }>) =>
    setReview((rv) => rv && { ...rv, suggested: rv.suggested.map((f, j) => (j === i ? { ...f, ...p } : f)) });

  return (
    <div className="card" style={{ marginTop: "1rem" }}>
      <h3 className="small">Import FAQs</h3>
      {!review ? (
        <>
          <div className="chips" style={{ marginBottom: ".6rem" }}>
            {SOURCES.map(([k, label]) => <button key={k} type="button" className={source === k ? "active" : ""} onClick={() => { setSource(k); setContent(""); }}>{label}</button>)}
          </div>
          <p className="hint">{SOURCES.find(([k]) => k === source)?.[2]}</p>
          <div className="form">
            {source === "url"
              ? <label>Page address<input type="url" placeholder="https://www.example.co.uk/faqs" value={content} onChange={(e) => setContent(e.target.value)} /></label>
              : <label>{source === "csv" ? "CSV rows" : "Text"}<textarea rows={8} value={content} onChange={(e) => setContent(e.target.value)} placeholder={source === "csv" ? "question,answer,category\nDo you deliver?,Yes — within 10 miles,delivery" : "Q: What are your opening hours?\nA: Mon–Fri 9am to 5pm.\n\nQ: Do you offer parking?\nA: Yes, free on site."} /></label>}
            <div className="two">
              <label>Category for new FAQs<input value={category} onChange={(e) => setCategory(e.target.value)} /></label>
            </div>
            {err && <p className="small" style={{ color: "var(--bad-fg)" }}>{err}</p>}
            <div className="row">
              <button type="button" className="primary" disabled={busy || !content.trim()} onClick={run}>{busy ? "Reading…" : "Find FAQs"}</button>
            </div>
          </div>
        </>
      ) : (
        <>
          <p className="hint">
            Found {review.suggested.length} new FAQ{review.suggested.length === 1 ? "" : "s"}
            {review.duplicates.length > 0 && <> ({review.duplicates.length} already in your assistant, skipped)</>}. Edit the answers, untick any you don&apos;t want, then apply — this saves a new version of your assistant.
          </p>
          {review.suggested.length === 0 && <p className="small muted">Nothing new was found. Try a different page or paste the questions directly.</p>}
          {review.suggested.map((f, i) => (
            <div className="list-row" key={i} style={{ opacity: f.keep ? 1 : 0.5, gridTemplateColumns: "auto 1.2fr 2fr 8rem" }}>
              <label className="small"><input type="checkbox" checked={f.keep} onChange={(e) => edit(i, { keep: e.target.checked })} /></label>
              <input value={f.question} onChange={(e) => edit(i, { question: e.target.value })} />
              <textarea value={f.answer} placeholder="Answer" onChange={(e) => edit(i, { answer: e.target.value })} />
              <input value={f.category} onChange={(e) => edit(i, { category: e.target.value })} />
            </div>
          ))}
          {err && <p className="small" style={{ color: "var(--bad-fg)" }}>{err}</p>}
          <div className="row" style={{ marginTop: ".6rem" }}>
            <button type="button" className="primary" disabled={busy || review.suggested.every((f) => !f.keep)} onClick={apply}>{busy ? "Applying…" : `Apply ${review.suggested.filter((f) => f.keep).length} FAQ${review.suggested.filter((f) => f.keep).length === 1 ? "" : "s"}`}</button>
            <button type="button" className="ghost" onClick={() => setReview(null)}>Back</button>
          </div>
        </>
      )}
    </div>
  );
}
