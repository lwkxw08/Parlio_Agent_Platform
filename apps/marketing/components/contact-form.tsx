"use client";

import { type FormEvent, useEffect, useState } from "react";
import { sendContact } from "@/lib/api";

const INTERESTS = [
  ["demo", "Book a personal demo"],
  ["starter", "Starter plan"],
  ["growth", "Growth plan"],
  ["scale", "Scale plan"],
  ["enterprise", "Enterprise / UK-sovereign"],
  ["partner", "Agency / reseller partnership"],
  ["other", "Something else"],
] as const;

export function ContactForm() {
  const [interest, setInterest] = useState<string>("demo");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const q = new URLSearchParams(window.location.search).get("interest");
    if (q && INTERESTS.some(([k]) => k === q)) setInterest(q);
  }, []);

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const fd = new FormData(e.currentTarget);
    const str = (k: string) => String(fd.get(k) ?? "").trim();
    setBusy(true); setError(null);
    const ok = await sendContact({
      name: str("name"),
      email: str("email"),
      company: str("company") || undefined,
      phone: str("phone") || undefined,
      interest,
      message: str("message"),
      website: str("website") || undefined,
    });
    setBusy(false);
    if (ok) setDone(true);
    else setError("Sorry — we couldn't send that. Please try again in a moment or email hello@parliotec.com.");
  }

  if (done) {
    return (
      <div className="success">
        <b>Thanks — we&apos;ve got it.</b> A real person will reply within one working day. If you asked for a demo,
        we&apos;ll suggest a couple of times.
      </div>
    );
  }

  return (
    <form onSubmit={(e) => void submit(e)} className="form-grid">
      <label className="field">Your name<input name="name" required maxLength={120} autoComplete="name" /></label>
      <label className="field">Work email<input name="email" type="email" required maxLength={200} autoComplete="email" /></label>
      <label className="field">Company<input name="company" maxLength={160} autoComplete="organization" /></label>
      <label className="field">Phone (optional)<input name="phone" maxLength={40} autoComplete="tel" /></label>
      <label className="field full">
        I&apos;m interested in
        <select value={interest} onChange={(e) => setInterest(e.target.value)}>
          {INTERESTS.map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
      </label>
      <label className="field full">
        Tell us about your calls
        <textarea name="message" required rows={5} maxLength={4000} placeholder="How many calls a day, what callers usually want, what you use for your diary today…" />
      </label>
      <label className="hp" aria-hidden="true">Website<input name="website" tabIndex={-1} autoComplete="off" /></label>
      {error && <div className="error-box full">{error}</div>}
      <div className="full" style={{ display: "flex", gap: "1rem", alignItems: "center", flexWrap: "wrap" }}>
        <button className="btn primary lg" type="submit" disabled={busy}>{busy ? "Sending…" : "Send message"}</button>
        <span className="muted small">We only use these details to reply to you. See our <a href="/legal/privacy/">privacy policy</a>.</span>
      </div>
    </form>
  );
}
