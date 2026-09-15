"use client";

import Link from "next/link";
import { useState } from "react";
import {
  type Assistant,
  type Insight,
  type Proposal,
  type QAOverview,
  type QAScore,
  type QASettings,
  type RegressionView,
  type Scenario,
  type SimulationRun,
  type VoiceClone,
  type VoiceCloneView,
  del,
  fetchQuality,
  fetchScenarios,
  fetchSimRuns,
  fetchVoiceClones,
  post,
  put,
  request,
  when,
} from "@/lib/api";
import Improve from "./improve";
import { humanize } from "@/app/breakdown";

const TABS = [["scores", "Call scores"], ["insights", "Insights"], ["simulate", "Simulation sandbox"], ["improve", "Regression pack & auto-improve"], ["voice", "Owner voice"]] as const;
type Tab = (typeof TABS)[number][0];

type Props = {
  tenant: string; canManage: boolean; overview: QAOverview; scenarios: Scenario[]; runs: SimulationRun[]; clones: VoiceCloneView | null; assistants: Assistant[];
  regression: RegressionView; proposals: Proposal[];
};

const scoreCls = (n: number) => (n >= 7 ? "ok" : n >= 5 ? "warn" : "bad");
const avg = (v: number | null) => (v == null ? "—" : v.toFixed(1));

export default function Quality(p: Props) {
  const [tab, setTab] = useState<Tab>("scores");
  const [msg, setMsg] = useState<string | null>(null);
  const flash = (m: string) => { setMsg(m); setTimeout(() => setMsg(null), 5000); };
  return (
    <>
      <div className="tabs">
        {TABS.map(([id, label]) => <button key={id} className={tab === id ? "active" : ""} onClick={() => setTab(id)}>{label}</button>)}
      </div>
      {msg && <p className="small" style={{ color: "var(--accent)" }}>{msg}</p>}
      {tab === "scores" && <Scores tenant={p.tenant} canManage={p.canManage} overview={p.overview} flash={flash} />}
      {tab === "insights" && <Insights tenant={p.tenant} canManage={p.canManage} initial={p.overview.insights} flash={flash} />}
      {tab === "simulate" && <Simulate tenant={p.tenant} canManage={p.canManage} scenarios={p.scenarios} runs={p.runs} assistants={p.assistants} flash={flash} />}
      {tab === "improve" && <Improve tenant={p.tenant} canManage={p.canManage} regression={p.regression} proposals={p.proposals} runs={p.runs} assistants={p.assistants} flash={flash} />}
      {tab === "voice" && <Voice tenant={p.tenant} canManage={p.canManage} view={p.clones} assistants={p.assistants} flash={flash} />}
    </>
  );
}

function Scores({ tenant, canManage, overview, flash }: { tenant: string; canManage: boolean; overview: QAOverview; flash: (m: string) => void }) {
  const [ov, setOv] = useState(overview);
  const [s, setS] = useState<QASettings>(overview.settings);
  const q = `?tenant_id=${tenant}`;
  const st = ov.stats;
  const refresh = async () => { const r = await fetchQuality(tenant); if (r) setOv(r); };
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    const r = await put<QASettings>(`/v1/quality/settings${q}`, { enabled: s.enabled, alert_below: s.alert_below, alert_on_hallucination: s.alert_on_hallucination, min_turns: s.min_turns });
    if (!r.ok) return flash(`Could not save: ${r.error}`);
    setS(r.data); flash("QA settings saved.");
  };
  const rescore = async (id: string) => {
    const r = await post<QAScore>(`/v1/quality/calls/${id}/rescore${q}`);
    flash(r ? `Rescored ${id}: ${r.overall}/10` : "Rescore failed");
    await refresh();
  };
  const kpi = (label: string, value: string | number, sub?: string) => (
    <div className="card"><div className="label">{label}</div><div className="value">{value}</div>{sub && <div className="small muted">{sub}</div>}</div>
  );
  return (
    <>
      <div className="grid">
        {kpi("Calls scored", st.scored)}
        {kpi("Average score", avg(st.avg_overall), "out of 10")}
        {kpi("Resolution", avg(st.avg_resolution))}
        {kpi("Tone", avg(st.avg_tone))}
        {kpi("Accuracy", avg(st.avg_accuracy))}
        {kpi("Hallucination risk", avg(st.avg_hallucination_risk), "lower is better")}
        {kpi("Low-score calls", st.low_score_calls, `below ${s.alert_below}`)}
        {kpi("Unanswered questions", st.unanswered_questions, "feed the Insights tab")}
      </div>
      <p className="hint" style={{ marginTop: "0.6rem" }}>
        Every answered call is scored after it ends on resolution, tone, accuracy and hallucination risk. Without an LLM key the
        deterministic heuristic scorer is used (scorer column); with one, the LLM scores and falls back to the heuristic on error.
      </p>
      <div className="section">
        <h2>Recent scores</h2>
        {ov.recent.length === 0 ? <p className="muted small">No scored calls yet — scores appear a few seconds after each call ends.</p> : (
          <table>
            <thead><tr><th>When</th><th>Call</th><th>Overall</th><th>Res.</th><th>Tone</th><th>Acc.</th><th>Halluc.</th><th>Flags</th><th>Notes</th><th>Scorer</th><th></th></tr></thead>
            <tbody>
              {ov.recent.map((r) => (
                <tr key={r.call_id}>
                  <td className="small">{when(r.created_at)}</td>
                  <td><Link href={`/calls/${r.call_id}`}>{r.call_id.slice(0, 10)}</Link></td>
                  <td><span className={`pill ${scoreCls(r.overall)}`}>{r.overall}</span></td>
                  <td>{r.resolution}</td><td>{r.tone}</td><td>{r.accuracy}</td><td>{r.hallucination_risk}</td>
                  <td className="small">{r.flags.map((f) => <span key={f} className={`pill ${f === "hallucination" || f === "rude" ? "bad" : "warn"}`} style={{ marginRight: 4 }}>{f}</span>)}</td>
                  <td className="small muted">{r.notes.join("; ")}</td>
                  <td className="small muted">{r.scorer}</td>
                  <td>{canManage && <button className="small" onClick={() => rescore(r.call_id)}>Rescore</button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <form className="section form" onSubmit={save}>
        <h2>Scoring &amp; alerts</h2>
        <label className="check"><input type="checkbox" checked={s.enabled} disabled={!canManage} onChange={(e) => setS({ ...s, enabled: e.target.checked })} /> Score calls automatically</label>
        <div className="row">
          <label>Alert when overall score is below
            <input type="number" min={0} max={10} value={s.alert_below} disabled={!canManage} onChange={(e) => setS({ ...s, alert_below: Number(e.target.value) })} />
          </label>
          <label>Minimum caller turns to score
            <input type="number" min={0} max={20} value={s.min_turns} disabled={!canManage} onChange={(e) => setS({ ...s, min_turns: Number(e.target.value) })} />
          </label>
        </div>
        <label className="check"><input type="checkbox" checked={s.alert_on_hallucination} disabled={!canManage} onChange={(e) => setS({ ...s, alert_on_hallucination: e.target.checked })} /> Also alert on hallucination flags</label>
        <p className="hint">Alerts go through the <Link href={`/integrations?tenant=${tenant}`}>notification rules</Link> (event <code>qa.low_score</code>: email, SMS or Slack).</p>
        {canManage && <button className="primary" type="submit">Save</button>}
      </form>
    </>
  );
}

function Insights({ tenant, canManage, initial, flash }: { tenant: string; canManage: boolean; initial: Insight[]; flash: (m: string) => void }) {
  const [rows, setRows] = useState(initial);
  const [edit, setEdit] = useState<Record<string, string>>({});
  const q = `?tenant_id=${tenant}`;
  const rebuild = async () => {
    const r = await post<Insight[]>(`/v1/quality/insights/rebuild${q}`);
    if (r) { setRows(r); flash(`${r.length} open insight${r.length === 1 ? "" : "s"}.`); }
  };
  const apply = async (i: Insight) => {
    const text = edit[i.id] ?? (i.kind === "faq" ? i.suggested_answer ?? "" : i.suggested_rule ?? "");
    if (!text.trim()) return flash("Write the answer or rule first.");
    const r = await request<Insight>(`/v1/quality/insights/${i.id}/apply${q}`, {
      method: "POST", body: JSON.stringify(i.kind === "faq" ? { answer: text, question: i.question } : { rule: text }),
    });
    if (!r.ok) return flash(`Could not apply: ${r.error}`);
    setRows((rs) => rs.filter((x) => x.id !== i.id));
    flash(`Applied to the assistant as version ${r.data.applied_version} — see Assistant Studio to review or roll back.`);
  };
  const dismiss = async (id: string) => {
    if (await post(`/v1/quality/insights/${id}/dismiss${q}`)) setRows((rs) => rs.filter((x) => x.id !== id));
  };
  return (
    <div className="section">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h2>Suggested FAQs &amp; rules</h2>
        {canManage && <button onClick={rebuild}>Rebuild from recent calls</button>}
      </div>
      <p className="hint">
        Questions callers asked that the assistant could not answer, clustered across calls. Fill in the answer (or rule) and apply —
        it is added to the live assistant as a new version, so it can be rolled back from Studio.
      </p>
      {rows.length === 0 ? <p className="muted small">Nothing open — the assistant answered everything it was asked recently.</p> : rows.map((i) => (
        <div key={i.id} className="card" style={{ marginBottom: "0.8rem" }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: "1rem", flexWrap: "wrap" }}>
            <div>
              <span className={`pill ${i.kind === "faq" ? "" : "warn"}`}>{i.kind === "faq" ? "FAQ" : "Rule"}</span>{" "}
              <strong>{i.question}</strong>
              <div className="small muted">Asked {i.count} time{i.count === 1 ? "" : "s"} · {i.call_ids.slice(0, 3).map((c) => <Link key={c} href={`/calls/${c}`} style={{ marginRight: 6 }}>{c.slice(0, 8)}</Link>)}</div>
            </div>
            <span className="small muted">{when(i.updated_at)}</span>
          </div>
          {i.examples.length > 1 && <ul className="small muted" style={{ margin: "0.4rem 0 0 1rem" }}>{i.examples.slice(0, 3).map((e, n) => <li key={n}>&ldquo;{e}&rdquo;</li>)}</ul>}
          {canManage && (
            <div className="form" style={{ marginTop: "0.6rem" }}>
              <label>{i.kind === "faq" ? "Answer the assistant should give" : "Business rule to add"}
                <textarea rows={2} value={edit[i.id] ?? (i.kind === "faq" ? i.suggested_answer ?? "" : i.suggested_rule ?? "")} onChange={(e) => setEdit({ ...edit, [i.id]: e.target.value })} placeholder={i.kind === "faq" ? "e.g. Yes — gas safety certificates are £75 and take about 45 minutes." : "e.g. Always offer a callback for certificate enquiries."} />
              </label>
              <div style={{ display: "flex", gap: "0.5rem" }}>
                <button className="primary" onClick={() => apply(i)}>Apply to assistant</button>
                <button onClick={() => dismiss(i.id)}>Dismiss</button>
              </div>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

const EMPTY: Omit<Scenario, "id" | "tenant_id" | "created_at" | "origin"> = {
  name: "", persona: "A polite first-time caller", goal: "", turns: ["Hi, I'd like to book an appointment please."],
  expect: { mentions: [], avoids: [], handoff: null, ticket: null, min_overall: 5 }, regression: false,
};

function Simulate({ tenant, canManage, scenarios, runs, assistants, flash }: {
  tenant: string; canManage: boolean; scenarios: Scenario[]; runs: SimulationRun[]; assistants: Assistant[]; flash: (m: string) => void;
}) {
  const [scs, setScs] = useState(scenarios);
  const [hist, setHist] = useState(runs);
  const [sel, setSel] = useState<string[]>(scenarios.map((s) => s.id));
  const [assistant, setAssistant] = useState(assistants[0]?.assistant_id ?? "");
  const [draftA, setDraftA] = useState("");
  const [draftB, setDraftB] = useState("");
  const [ab, setAb] = useState(false);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState<SimulationRun | null>(runs[0] ?? null);
  const [form, setForm] = useState(EMPTY);
  const [editing, setEditing] = useState<string | null>(null);
  const q = `?tenant_id=${tenant}`;

  const base = assistants.find((a) => a.assistant_id === assistant);
  const withInstructions = (extra: string) => (base ? { ...base, instructions: `${base.instructions}\n\n${extra}`.trim() } : null);

  const run = async () => {
    if (!assistant || sel.length === 0) return flash("Pick an assistant and at least one scenario.");
    setBusy(true);
    const body: Record<string, unknown> = { assistant_id: assistant, scenario_ids: sel };
    if (draftA.trim()) body.draft = withInstructions(draftA);
    if (ab && draftB.trim()) body.variant_b = withInstructions(draftB);
    const r = await request<SimulationRun>(`/v1/quality/simulate${q}`, { method: "POST", body: JSON.stringify(body) });
    setBusy(false);
    if (!r.ok) return flash(`Run failed: ${r.error}`);
    setOpen(r.data); setHist([r.data, ...hist]);
    const passed = r.data.results.filter((x) => x.passed).length;
    flash(`${passed}/${r.data.results.length} passed${r.data.winner ? ` · winner: variant ${r.data.winner}` : ""}`);
  };
  const saveScenario = async (e: React.FormEvent) => {
    e.preventDefault();
    const r = await put<Scenario>(`/v1/quality/scenarios${q}`, {
      id: editing, name: form.name, persona: form.persona, goal: form.goal, turns: form.turns.filter((t) => t.trim()), regression: form.regression,
      expect: { ...form.expect, mentions: form.expect.mentions.filter(Boolean), avoids: form.expect.avoids.filter(Boolean) },
    });
    if (!r.ok) return flash(`Could not save: ${r.error}`);
    const next = await fetchScenarios(tenant);
    if (next) { setScs(next); setSel((s) => (s.includes(r.data.id) ? s : [...s, r.data.id])); }
    setForm(EMPTY); setEditing(null); flash("Scenario saved.");
  };
  const remove = async (id: string) => {
    if (await del(`/v1/quality/scenarios/${id}${q}`)) { setScs((s) => s.filter((x) => x.id !== id)); setSel((s) => s.filter((x) => x !== id)); }
  };
  const keepFailures = async (run: SimulationRun) => {
    const r = await request<Scenario[]>(`/v1/quality/regression/from-run/${run.id}${q}`, { method: "POST" });
    if (!r.ok) return flash(`Could not save: ${r.error}`);
    const next = await fetchScenarios(tenant);
    if (next) setScs(next);
    flash(r.data.length ? `${r.data.length} failed scenario${r.data.length === 1 ? "" : "s"} added to the regression pack — they now run on every Studio save.` : "Every failed scenario is already in the regression pack.");
  };
  const list = (v: string[]) => v.join(", ");
  const parse = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);

  return (
    <>
      <div className="two-col">
        <div className="section">
          <h2>Scripted test callers</h2>
          <p className="hint">Each scenario plays a caller's lines against the assistant in text mode and checks the expectations. Use them before publishing a Studio change.</p>
          {scs.length === 0 && <p className="muted small">No scenarios yet — add one on the right.</p>}
          {scs.map((s) => (
            <label key={s.id} className="check list-row" style={{ alignItems: "flex-start" }}>
              <input type="checkbox" checked={sel.includes(s.id)} onChange={(e) => setSel(e.target.checked ? [...sel, s.id] : sel.filter((x) => x !== s.id))} />
              <div style={{ flex: 1 }}>
                <strong>{s.name}</strong> {s.regression && <span className="pill" title="Runs on every Studio save">regression</span>} <span className="small muted">— {s.persona}{s.goal ? `; wants to ${s.goal}` : ""}</span>
                <div className="small muted">{s.turns.length} turn{s.turns.length === 1 ? "" : "s"}{s.expect.mentions.length ? ` · must mention ${list(s.expect.mentions)}` : ""}{s.expect.handoff != null ? ` · handoff ${s.expect.handoff ? "expected" : "not expected"}` : ""}</div>
              </div>
              {canManage && (
                <span style={{ display: "flex", gap: 4 }}>
                  <button className="small" type="button" onClick={() => { setEditing(s.id); setForm({ name: s.name, persona: s.persona, goal: s.goal, turns: s.turns, expect: s.expect, regression: s.regression }); }}>Edit</button>
                  <button className="small" type="button" onClick={() => remove(s.id)}>Delete</button>
                </span>
              )}
            </label>
          ))}
          <h2 style={{ marginTop: "1rem" }}>Run</h2>
          <div className="form">
            <label>Assistant
              <select value={assistant} onChange={(e) => setAssistant(e.target.value)}>{assistants.map((a) => <option key={a.assistant_id} value={a.assistant_id}>{a.name}</option>)}</select>
            </label>
            <label>Draft instructions to test (variant A — leave blank to test the live config)
              <textarea rows={3} value={draftA} onChange={(e) => setDraftA(e.target.value)} placeholder="Extra instructions appended to the live prompt, e.g. 'Always offer the next available slot before asking for details.'" />
            </label>
            <label className="check"><input type="checkbox" checked={ab} onChange={(e) => setAb(e.target.checked)} /> A/B compare against a second prompt</label>
            {ab && (
              <label>Variant B instructions
                <textarea rows={3} value={draftB} onChange={(e) => setDraftB(e.target.value)} />
              </label>
            )}
            <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
              <button className="primary" onClick={run} disabled={busy || !assistant}>{busy ? "Running…" : "Run simulation"}</button>
              <span className="small muted">Text simulation. For a voice run-through use the click-to-talk button on the <Link href={`/inbox?tenant=${tenant}`}>web chat widget</Link>.</span>
            </div>
          </div>
        </div>
        <form className="section form" onSubmit={saveScenario}>
          <h2>{editing ? "Edit scenario" : "New scenario"}</h2>
          <label>Name<input value={form.name} required onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Landlord needs a gas cert" /></label>
          <div className="row">
            <label>Persona<input value={form.persona} onChange={(e) => setForm({ ...form, persona: e.target.value })} /></label>
            <label>Goal<input value={form.goal} onChange={(e) => setForm({ ...form, goal: e.target.value })} placeholder="book a gas safety check" /></label>
          </div>
          <label>Caller lines (one per line, in order)
            <textarea rows={4} value={form.turns.join("\n")} onChange={(e) => setForm({ ...form, turns: e.target.value.split("\n") })} />
          </label>
          <div className="row">
            <label>Reply must mention (comma-separated)<input value={list(form.expect.mentions)} onChange={(e) => setForm({ ...form, expect: { ...form.expect, mentions: parse(e.target.value) } })} placeholder="certificate, £" /></label>
            <label>Reply must avoid<input value={list(form.expect.avoids)} onChange={(e) => setForm({ ...form, expect: { ...form.expect, avoids: parse(e.target.value) } })} placeholder="guarantee, free" /></label>
          </div>
          <div className="row">
            <label>Handoff to a human
              <select value={form.expect.handoff == null ? "" : String(form.expect.handoff)} onChange={(e) => setForm({ ...form, expect: { ...form.expect, handoff: e.target.value === "" ? null : e.target.value === "true" } })}>
                <option value="">don&apos;t care</option><option value="true">expected</option><option value="false">must not</option>
              </select>
            </label>
            <label>Minimum QA score<input type="number" min={0} max={10} value={form.expect.min_overall} onChange={(e) => setForm({ ...form, expect: { ...form.expect, min_overall: Number(e.target.value) } })} /></label>
          </div>
          <label className="check"><input type="checkbox" checked={form.regression} onChange={(e) => setForm({ ...form, regression: e.target.checked })} /> Regression test — run on every Studio save and block the save if it starts failing</label>
          {canManage && (
            <div style={{ display: "flex", gap: "0.5rem" }}>
              <button className="primary" type="submit">{editing ? "Save changes" : "Add scenario"}</button>
              {editing && <button type="button" onClick={() => { setEditing(null); setForm(EMPTY); }}>Cancel</button>}
            </div>
          )}
        </form>
      </div>
      <div className="section">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "0.5rem" }}>
          <h2>Results</h2>
          {hist.length > 0 && (
            <select value={open?.id ?? ""} onChange={(e) => setOpen(hist.find((h) => h.id === e.target.value) ?? null)} style={{ maxWidth: 320 }}>
              {hist.map((h) => <option key={h.id} value={h.id}>{when(h.created_at)} — {h.results.filter((x) => x.passed).length}/{h.results.length} passed{h.winner ? ` (winner ${h.winner})` : ""}</option>)}
            </select>
          )}
        </div>
        {!open ? <p className="muted small">No runs yet.</p> : (
          <>
            {open.winner && <p className="small"><span className="pill ok">Variant {open.winner} wins</span> higher average QA score across the scenarios.</p>}
            {canManage && open.results.some((r) => r.label === "A" && !r.passed) && (
              <p className="small" style={{ display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
                <button className="small" type="button" onClick={() => keepFailures(open)}>Keep failures as regression tests</button>
                <span className="muted">then draft fixes under the &ldquo;Regression pack &amp; auto-improve&rdquo; tab.</span>
              </p>
            )}
            {open.results.map((r, n) => (
              <div key={n} className="card" style={{ marginBottom: "0.8rem" }}>
                <div style={{ display: "flex", justifyContent: "space-between", flexWrap: "wrap", gap: "0.5rem" }}>
                  <strong>{r.scenario_name} <span className="pill">{r.label}</span> <span className="small muted">{r.config_source} · {r.agent}</span></strong>
                  <span><span className={`pill ${r.passed ? "ok" : "bad"}`}>{r.passed ? "passed" : "failed"}</span> <span className={`pill ${scoreCls(r.score.overall)}`}>QA {r.score.overall}</span></span>
                </div>
                {r.failures.length > 0 && <ul className="small" style={{ color: "var(--bad-fg)", margin: "0.3rem 0 0 1rem" }}>{r.failures.map((f, i) => <li key={i}>{f}</li>)}</ul>}
                <p className="small muted" style={{ margin: "0.3rem 0 0" }}>
                  Resolution {r.score.resolution} · Tone {r.score.tone} · Accuracy {r.score.accuracy} · Hallucination risk {r.score.hallucination_risk} · scored by {r.score.scorer}
                </p>
                {r.score.notes.length > 0 && <ul className="small muted" style={{ margin: "0.2rem 0 0 1rem" }}>{r.score.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>}
                <div className="transcript" style={{ marginTop: "0.5rem" }}>
                  {r.turns.map((t, i) => (
                    <div key={i}>
                      <div className="msg user"><span className="small muted">Caller</span> {t.caller}</div>
                      <div className="msg assistant"><span className="small muted">Assistant{t.handoff ? " · handoff" : ""}{t.ticket ? " · ticket" : ""}</span> {t.assistant}</div>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </>
        )}
      </div>
    </>
  );
}

function Voice({ tenant, canManage, view, assistants, flash }: { tenant: string; canManage: boolean; view: VoiceCloneView | null; assistants: Assistant[]; flash: (m: string) => void }) {
  const [v, setV] = useState(view);
  const [name, setName] = useState("");
  const [assistant, setAssistant] = useState(assistants[0]?.assistant_id ?? "");
  const [consent, setConsent] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const q = `?tenant_id=${tenant}`;
  const refresh = async () => { const r = await fetchVoiceClones(tenant); if (r) setV(r); };
  if (!v) return <p className="muted">Voice cloning unavailable.</p>;
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!file) return flash("Choose an audio sample.");
    setBusy(true);
    const buf = new Uint8Array(await file.arrayBuffer());
    let bin = "";
    for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode(...buf.subarray(i, i + 0x8000));
    const seconds = await new Promise<number>((res) => {
      const a = document.createElement("audio");
      a.preload = "metadata";
      a.onloadedmetadata = () => res(Number.isFinite(a.duration) ? a.duration : 30);
      a.onerror = () => res(30);
      a.src = URL.createObjectURL(file);
    });
    const r = await request<VoiceClone>(`/v1/quality/voice-clones${q}`, {
      method: "POST", body: JSON.stringify({ assistant_id: assistant, name, consent, sample_base64: btoa(bin), sample_seconds: seconds }),
    });
    setBusy(false);
    if (!r.ok) return flash(`Could not create: ${r.error}`);
    setName(""); setFile(null); setConsent(false); flash(r.data.status === "ready" ? "Voice profile created." : `Status: ${r.data.status}`);
    await refresh();
  };
  const activate = async (id: string) => { if (await post(`/v1/quality/voice-clones/${id}/activate${q}`)) { flash("Assistant now uses this voice."); await refresh(); } else flash("Activation failed"); };
  const remove = async (id: string) => { if (await del(`/v1/quality/voice-clones/${id}${q}`)) { flash("Voice profile and sample deleted."); await refresh(); } };
  return (
    <>
      <div className="section">
        <h2>Owner voice cloning</h2>
        <p className={`small ${v.live ? "" : "muted"}`}>
          <span className={`pill ${v.live ? "ok" : "warn"}`}>{v.live ? `Provider: ${v.provider}` : "Simulated — no cloning provider configured"}</span>{" "}
          {v.live ? "Uploaded samples are sent to the configured provider to build a voice." : "Consent and samples are recorded and the profile can be activated, but the assistant keeps its stock voice until a cloning provider is configured on the server."}
        </p>
        {v.clones.length === 0 ? <p className="muted small">No voice profiles.</p> : (
          <table>
            <thead><tr><th>Name</th><th>Assistant</th><th>Consented by</th><th>Sample</th><th>Status</th><th></th></tr></thead>
            <tbody>
              {v.clones.map((c) => (
                <tr key={c.id}>
                  <td>{c.name}</td><td>{c.assistant_id}</td><td className="small">{c.consent_by}<div className="muted">{when(c.consent_at)}</div></td>
                  <td className="small">{Math.round(c.sample_seconds)}s · {c.provider}</td>
                  <td><span className={`pill ${c.status === "ready" ? "ok" : c.status === "failed" ? "bad" : "warn"}`}>{humanize(c.status)}</span>{c.error && <div className="small muted">{c.error}</div>}</td>
                  <td>{canManage && <span style={{ display: "flex", gap: 4 }}><button className="small" disabled={c.status !== "ready"} onClick={() => activate(c.id)}>Use for assistant</button><button className="small" onClick={() => remove(c.id)}>Delete</button></span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {canManage && (
        <form className="section form" onSubmit={submit}>
          <h2>Add a voice</h2>
          <div className="row">
            <label>Name<input value={name} required onChange={(e) => setName(e.target.value)} placeholder="Keith — reception" /></label>
            <label>Assistant<select value={assistant} onChange={(e) => setAssistant(e.target.value)}>{assistants.map((a) => <option key={a.assistant_id} value={a.assistant_id}>{a.name}</option>)}</select></label>
          </div>
          <label>Audio sample (30–90 s of clear speech, wav/mp3/m4a)<input type="file" accept="audio/*" onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></label>
          <div className="card" style={{ background: "var(--panel-2, transparent)" }}>
            <p className="small">{v.consent_statement}</p>
            <label className="check"><input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} /> I am the person in this recording (or have their written permission) and agree to the statement above.</label>
          </div>
          <button className="primary" type="submit" disabled={busy || !consent || !file || !name}>{busy ? "Uploading…" : "Create voice profile"}</button>
          <p className="hint">The sample is passed to the voice provider and not kept by Parlio; only the consent record and the provider&apos;s voice reference are stored. Consent is written to the audit log.</p>
        </form>
      )}
    </>
  );
}
