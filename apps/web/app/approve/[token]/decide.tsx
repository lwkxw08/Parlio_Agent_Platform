"use client";

import { useEffect, useState } from "react";
import { type PublicApproval, decidePublicApproval, when } from "@/lib/api";
import { humanize } from "@/app/breakdown";

export default function Decide({ token, initial }: { token: string; initial: PublicApproval }) {
  const [a, setA] = useState(initial);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const left = Math.max(0, Math.floor((new Date(a.expires_at).getTime() - now) / 1000));
  const amount = a.amount !== null ? new Intl.NumberFormat("en-GB", { style: "currency", currency: a.currency || "GBP" }).format(a.amount) : null;

  const decide = async (approve: boolean) => {
    setBusy(true); setErr(null);
    const r = await decidePublicApproval(token, approve, name || undefined);
    if (r.ok) setA(r.data); else setErr(r.error);
    setBusy(false);
  };

  const pending = a.status === "pending" && left > 0;
  return (
    <div className="approve-page">
      <div className="section">
        <p className="muted small" style={{ margin: 0 }}>Parlio · approval requested {when(a.requested_at)}</p>
        <h2 style={{ marginTop: "0.4rem" }}><span className="pill warn" style={{ marginRight: "0.4rem" }}>{humanize(a.kind)}</span>{a.title}</h2>
        {amount && <p style={{ fontSize: "1.6rem", fontWeight: 600, margin: "0.4rem 0" }}>{amount}</p>}
        {a.details && <p>{a.details}</p>}
        {a.caller && <p className="muted small">Caller: {a.caller}{a.call_id ? " (on the line now)" : ""}</p>}
        {pending ? (
          <>
            <p className="muted small">The assistant is holding the caller — {Math.floor(left / 60)}:{String(left % 60).padStart(2, "0")} left to decide.</p>
            <div className="form">
              <label>Your name (optional)<input value={name} onChange={(e) => setName(e.target.value)} placeholder="so the team knows who decided" /></label>
            </div>
            <div className="big">
              <button className="primary" disabled={busy} onClick={() => decide(true)}>Approve</button>
              <button className="danger" disabled={busy} onClick={() => decide(false)}>Reject</button>
            </div>
            {err && <p className="muted small" style={{ color: "var(--bad-fg)" }}>{err}</p>}
          </>
        ) : (
          <p>
            <span className={`pill ${a.status === "approved" ? "ok" : a.status === "rejected" ? "bad" : "warn"}`}>{a.status === "pending" ? "expired" : a.status}</span>
            {a.decided_by && <span className="muted small"> · by {a.decided_by} {a.decided_at ? when(a.decided_at) : ""}</span>}
          </p>
        )}
      </div>
      <p className="muted small" style={{ textAlign: "center" }}>This link is single-use and expires automatically.</p>
    </div>
  );
}
