"use client";

import Link from "next/link";
import { useState } from "react";
import {
  type ChatWidgetInfo, type Plan, type Questionnaire, type SetupChecklist, type SyntheticRun,
  fetchChecklist, gbp, runSynthetic, when,
} from "@/lib/api";

const TASK_LABEL: Record<string, string> = {
  faqs: "answer questions", messages: "take messages", book: "book appointments", transfer: "transfer to staff", info: "give business info",
  qualify: "qualify leads", payments: "take payments", outbound: "call people back", webchat: "web chat",
};

export default function Setup({ tenant, initial, plans, questionnaire, widget, canManage }: {
  tenant: string; initial: SetupChecklist; plans: Plan[]; questionnaire: Questionnaire | null; widget: ChatWidgetInfo | null; canManage: boolean;
}) {
  const [cl, setCl] = useState(initial);
  const [run, setRun] = useState<SyntheticRun | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const plan = plans.find((p) => p.id === cl.plan_id);
  const pct = cl.total ? Math.round((cl.completed / cl.total) * 100) : 0;

  const testCall = async () => {
    setBusy(true); setMsg(null);
    const r = await runSynthetic(tenant);
    setBusy(false);
    if (!r.ok) return setMsg(`Test call failed to start: ${r.error}`);
    setRun(r.data);
    const next = await fetchChecklist(tenant);
    if (next) setCl(next);
  };

  return (
    <>
      <div className="grid">
        <div className="card">
          <div className="label">Progress</div>
          <div className="value">{cl.completed}/{cl.total} <span className="muted small">({pct}%)</span></div>
          <div className="small muted">{cl.live ? "Your assistant is taking calls" : cl.next_step ? `Next: ${cl.next_step.title}` : "All done"}</div>
        </div>
        <div className="card">
          <div className="label">Plan</div>
          <div className="value">{plan?.name ?? cl.plan_id}</div>
          <div className="small muted">{cl.trial_ends_at ? `Free trial until ${new Date(cl.trial_ends_at).toLocaleDateString("en-GB")}` : plan ? `${gbp(plan.monthly_pence)}/mo` : ""} · <Link href={`/billing?tenant=${tenant}&tab=plan`}>manage</Link></div>
        </div>
        <div className="card">
          <div className="label">Status</div>
          <div className="value"><span className={`pill ${cl.live ? "ok" : "warn"}`}>{cl.live ? "Live" : "Not live yet"}</span></div>
          <div className="small muted">{cl.live ? "Real calls have been answered" : "Connect a number and forward your line to go live"}</div>
        </div>
      </div>

      {questionnaire && (
        <p className="hint" style={{ marginTop: "1rem" }}>
          You told us: {questionnaire.monthly_calls} calls a month · {questionnaire.tasks.map((t) => TASK_LABEL[t] ?? t).join(", ")} · {questionnaire.team_size} {questionnaire.team_size === 1 ? "person" : "people"} taking calls.
        </p>
      )}

      <div className="section">
        <h2>Checklist</h2>
        <table>
          <tbody>
            {cl.items.map((i) => (
              <tr key={i.key} className={i.done ? "muted" : ""}>
                <td style={{ width: "2.2rem" }}><span className={`pill ${i.done ? "ok" : i.optional ? "" : "warn"}`}>{i.done ? "✓" : i.optional ? "opt" : "•"}</span></td>
                <td><strong>{i.title}</strong><div className="small muted">{i.detail}</div></td>
                <td style={{ textAlign: "right" }}>
                  {i.key === "test_call"
                    ? <a className="btn" href="#test">{i.done ? "Test again" : "Test now"}</a>
                    : <Link className="btn" href={i.href.includes("?") ? `${i.href}&tenant=${tenant}` : `${i.href}?tenant=${tenant}`}>{i.done ? "Review" : "Set up"}</Link>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="section" id="test">
        <h2>One-click test call</h2>
        <p className="hint">
          Runs a simulated caller through your assistant (greeting, an FAQ, a booking and a transfer request) and checks the answers — no phone needed.
          Once your number is connected, ring it to hear the assistant live.
        </p>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <button className="primary" disabled={busy || !canManage} onClick={testCall}>{busy ? "Calling…" : "Run test call"}</button>
          {widget?.enabled && widget.voice_enabled && <a className="btn" href={widget.embed_url} target="_blank" rel="noreferrer">Talk to it in your browser</a>}
          <Link className="btn" href={`/quality?tenant=${tenant}&tab=simulate`}>Custom test scenarios</Link>
          <Link className="btn" href="/launch">Forward your real number</Link>
        </div>
        {msg && <p className="small" style={{ color: "var(--bad-fg)" }}>{msg}</p>}
        {run && (
          <div className="card" style={{ marginTop: "1rem" }}>
            <div className="row" style={{ justifyContent: "space-between" }}>
              <strong>Test call {run.passed ? "passed" : "found issues"}</strong>
              <span className={`pill ${run.passed ? "ok" : "bad"}`}>{run.passed ? "passing" : "failing"}</span>
            </div>
            <div className="small muted">{when(run.created_at)} · {run.duration_ms} ms</div>
            <ul className="small">
              {run.checks.map((c) => <li key={c.path}><span className={`pill ${c.passed ? "ok" : "bad"}`}>{c.path}</span> {c.detail}</li>)}
            </ul>
            {!run.passed && <p className="small">Fix the answers in <Link href={`/assistant`}>Assistant Studio</Link>, then run again.</p>}
          </div>
        )}
      </div>
    </>
  );
}
