"use client";

import Link from "next/link";
import { useState } from "react";
import {
  type Assistant,
  type Proposal,
  type RegressionCheck,
  type RegressionView,
  type Scenario,
  type SimulationRun,
  fetchProposals,
  fetchRegression,
  request,
  when,
} from "@/lib/api";

type Props = {
  tenant: string; canManage: boolean; regression: RegressionView; proposals: Proposal[]; runs: SimulationRun[]; assistants: Assistant[]; flash: (m: string) => void;
};

const delta = (b: number, a: number) => (a === b ? `${a}` : `${b} → ${a}`);

export default function Improve({ tenant, canManage, regression, proposals, runs, assistants, flash }: Props) {
  const [pack, setPack] = useState<Scenario[]>(regression.pack);
  const [checks, setChecks] = useState<RegressionCheck[]>(regression.checks);
  const [props, setProps] = useState<Proposal[]>(proposals);
  const [assistant, setAssistant] = useState(assistants[0]?.assistant_id ?? "");
  const [runId, setRunId] = useState(runs[0]?.id ?? "");
  const [busy, setBusy] = useState(false);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const q = `?tenant_id=${tenant}`;

  const refresh = async () => {
    const [r, p] = await Promise.all([fetchRegression(tenant), fetchProposals(tenant)]);
    if (r) { setPack(r.pack); setChecks(r.checks); }
    if (p) setProps(p);
  };
  const drop = async (id: string) => {
    const r = await request<Scenario>(`/v1/quality/scenarios/${id}/regression${q}&on=false`, { method: "POST" });
    if (!r.ok) return flash(`Could not update: ${r.error}`);
    setPack((p) => p.filter((s) => s.id !== id));
  };
  const propose = async () => {
    if (!assistant) return flash("Pick an assistant.");
    setBusy(true);
    const r = await request<Proposal[]>(`/v1/quality/improve/propose${q}`, {
      method: "POST", body: JSON.stringify({ assistant_id: assistant, run_id: runId || null }),
    });
    setBusy(false);
    if (!r.ok) return flash(`Could not draft fixes: ${r.error}`);
    flash(r.data.length ? `${r.data.length} fix${r.data.length === 1 ? "" : "es"} proposed — each one was re-tested and improves its scenario without breaking the pack.` : "No safe fixes found: every candidate either didn't fix the scenario or broke a regression test.");
    await refresh();
  };
  const decide = async (p: Proposal, action: "approve" | "reject") => {
    const body = action === "approve" && edits[p.id] != null ? JSON.stringify({ text: edits[p.id] }) : undefined;
    const r = await request<Proposal>(`/v1/quality/improve/proposals/${p.id}/${action}${q}`, { method: "POST", body });
    if (!r.ok) return flash(`Failed: ${r.error}`);
    flash(action === "approve" ? `Applied as assistant version ${r.data.applied_version}.` : "Proposal rejected.");
    await refresh();
  };

  const open = props.filter((p) => p.status === "proposed");
  const done = props.filter((p) => p.status !== "proposed").slice(0, 10);

  return (
    <>
      <div className="two-col">
        <div className="section">
          <h2>Regression pack</h2>
          <p className="hint">
            These scripted callers run automatically every time you save in Assistant Studio. A save is blocked if a test that passes today would fail on the new version.
            Add tests from a failed simulation (Simulation sandbox → &ldquo;Keep failures as regression tests&rdquo;) or by ticking &ldquo;Regression test&rdquo; on a scenario.
          </p>
          {pack.length === 0 && <p className="muted small">No regression tests yet — every Studio save publishes straight away.</p>}
          {pack.map((s) => (
            <div key={s.id} className="list-row" style={{ alignItems: "flex-start" }}>
              <div style={{ flex: 1 }}>
                <strong>{s.name}</strong>
                <div className="small muted">
                  {s.turns.length} turn{s.turns.length === 1 ? "" : "s"}
                  {s.expect.mentions.length ? ` · must mention ${s.expect.mentions.join(", ")}` : ""}
                  {s.expect.handoff != null ? ` · handoff ${s.expect.handoff ? "expected" : "not expected"}` : ""}
                  {s.origin?.startsWith("call:") && <> · from <Link href={`/calls/${s.origin.slice(5)}`}>a real call</Link></>}
                  {s.origin?.startsWith("run:") && " · from a failed simulation"}
                </div>
              </div>
              {canManage && <button className="small" type="button" onClick={() => drop(s.id)}>Remove from pack</button>}
            </div>
          ))}
        </div>
        <div className="section">
          <h2>Publish checks</h2>
          <p className="hint">Every Studio save that ran the pack, newest first.</p>
          {checks.length === 0 && <p className="muted small">No checks yet.</p>}
          {checks.slice(0, 12).map((c) => (
            <div key={c.id} className="list-row" style={{ alignItems: "flex-start" }}>
              <div style={{ flex: 1 }}>
                <span className={`pill ${c.blocked ? (c.forced ? "warn" : "bad") : "ok"}`}>{c.blocked ? (c.forced ? "forced" : "blocked") : "passed"}</span>{" "}
                <span className="small">{when(c.created_at)} · {c.candidate_passed}/{c.pack_size} passed{c.published_version != null ? ` · published v${c.published_version}` : ""}</span>
                {c.regressions.length > 0 && <ul className="small" style={{ color: "var(--bad-fg)", margin: "0.2rem 0 0 1rem" }}>{c.regressions.map((r, i) => <li key={i}>{r}</li>)}</ul>}
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="section">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "0.5rem" }}>
          <h2>Auto-improve</h2>
          {canManage && (
            <span style={{ display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
              <select value={assistant} onChange={(e) => setAssistant(e.target.value)}>{assistants.map((a) => <option key={a.assistant_id} value={a.assistant_id}>{a.name}</option>)}</select>
              <select value={runId} onChange={(e) => setRunId(e.target.value)} style={{ maxWidth: 300 }}>
                {runs.length === 0 && <option value="">No simulation runs yet</option>}
                {runs.map((h) => <option key={h.id} value={h.id}>{when(h.created_at)} — {h.results.filter((x) => x.passed).length}/{h.results.length} passed</option>)}
              </select>
              <button className="primary" onClick={propose} disabled={busy || !runId}>{busy ? "Drafting and re-testing…" : "Draft fixes for failures"}</button>
            </span>
          )}
        </div>
        <p className="hint">
          Takes the failed scenarios from a simulation run, drafts an FAQ or rule for each, re-runs that scenario plus the whole regression pack against the draft,
          and only shows you fixes that pass. Nothing changes until you approve — approving saves a new assistant version you can roll back.
        </p>
        {open.length === 0 && <p className="muted small">No proposals waiting.</p>}
        {open.map((p) => (
          <div key={p.id} className="card" style={{ marginBottom: "0.8rem" }}>
            <div style={{ display: "flex", justifyContent: "space-between", flexWrap: "wrap", gap: "0.5rem" }}>
              <strong>{p.title} <span className="pill">{p.kind === "faq" ? "FAQ" : "rule"}</span> <span className="small muted">{p.draft_source === "llm" ? "AI draft" : "template"}</span></strong>
              <span>
                <span className="pill ok">{p.scenario_name}: {p.delta.before_passed ? "pass" : "fail"} → {p.delta.after_passed ? "pass" : "fail"}</span>{" "}
                <span className="pill">QA {delta(p.delta.before_score, p.delta.after_score)}</span>{" "}
                <span className="pill">pack {p.check.candidate_passed}/{p.check.pack_size}</span>
              </span>
            </div>
            <p className="small muted" style={{ margin: "0.3rem 0" }}>Was failing because: {p.failures_before.join("; ")}</p>
            {p.kind === "faq" && <p className="small" style={{ margin: "0.2rem 0" }}><strong>Q:</strong> {p.question}</p>}
            <textarea rows={3} value={edits[p.id] ?? p.text} onChange={(e) => setEdits({ ...edits, [p.id]: e.target.value })} disabled={!canManage} />
            {canManage && (
              <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.4rem" }}>
                <button className="primary" onClick={() => decide(p, "approve")}>Approve &amp; publish</button>
                <button onClick={() => decide(p, "reject")}>Reject</button>
                {edits[p.id] != null && edits[p.id] !== p.text && <span className="small muted">Edited wording will be published as-is (not re-tested).</span>}
              </div>
            )}
          </div>
        ))}
        {done.length > 0 && (
          <>
            <h3 style={{ marginTop: "1rem" }}>History</h3>
            {done.map((p) => (
              <div key={p.id} className="list-row small">
                <span className={`pill ${p.status === "approved" ? "ok" : ""}`}>{p.status}</span>
                <span style={{ flex: 1 }}>{p.title}{p.applied_version != null ? ` · v${p.applied_version}` : ""}</span>
                <span className="muted">{when(p.updated_at)}</span>
              </div>
            ))}
          </>
        )}
      </div>
    </>
  );
}
