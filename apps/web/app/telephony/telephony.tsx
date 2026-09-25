"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  type Assistant,
  type DdiRoute,
  type SetupChecklist,
  type IssuedCredentials,
  type ProviderGuide,
  type SipTrunk,
  type TenantNumber,
  type TestCallResult,
  type TrunkMode,
  type TrunkView,
  del,
  fetchChecklist,
  fetchNumbers,
  post,
  request,
  when,
} from "@/lib/api";
import { humanize } from "@/app/breakdown";
import { NumberPicker, provisionedMessage } from "@/app/number-picker";

const MODES: { id: TrunkMode; title: string; blurb: string }[] = [
  { id: "forward", title: "Forward to your ParlioTec number", blurb: "Keep your provider. Forward calls (always or on no-answer) to the number ParlioTec gives you. No SIP setup." },
  { id: "pbx", title: "Connect your PBX", blurb: "ParlioTec issues SIP credentials for 3CX, FreePBX, Gamma Horizon, RingCentral, BT Cloud Voice… Your PBX sends calls to us and extension transfers stay internal." },
  { id: "byo_register", title: "Use your SIP account", blurb: "Give us your provider login (Voipfone, Sipgate, Gamma…). ParlioTec registers as that account so your DDI rings straight into the assistant and transfers use your caller ID." },
];
const WHEN: [DdiRoute["when"], string][] = [["always", "Always"], ["out_of_hours", "Out of hours only"], ["no_answer", "On no-answer"]];
const CODECS = ["PCMA", "PCMU", "opus"];

type Form = {
  name: string; mode: TrunkMode; provider_preset: string; pbx_address: string; allowed_ips: string;
  registrar: string; username: string; auth_username: string; outbound_proxy: string; password: string;
  transport: "udp" | "tcp" | "tls"; codecs: string[]; dtmf: string; srtp: boolean; max_concurrent_calls: number; ddis: DdiRoute[];
};

const empty = (mode: TrunkMode, assistant: string): Form => ({
  name: "Main trunk", mode, provider_preset: "", pbx_address: "", allowed_ips: "", registrar: "", username: "", auth_username: "",
  outbound_proxy: "", password: "", transport: "udp", codecs: ["PCMA", "opus"], dtmf: "rfc2833", srtp: false, max_concurrent_calls: 2,
  ddis: [{ e164: "", assistant_id: assistant, department: null, when: "always", label: null }],
});

const regPill = (t: SipTrunk) => {
  const s = t.registration.state;
  const cls = s === "registered" || s === "not_required" ? "ok" : s === "failed" ? "bad" : "warn";
  return <span className={`pill ${cls}`} title={t.registration.detail ?? undefined}>{s.replace("_", " ")}</span>;
};

const prettyUk = (e164: string) => {
  if (!e164.startsWith("+44")) return e164;
  const n = "0" + e164.slice(3);
  if (n.startsWith("02")) return `${n.slice(0, 3)} ${n.slice(3, 7)} ${n.slice(7)}`;
  return `${n.slice(0, 5)} ${n.slice(5)}`;
};

type Operator = { id: string; name: string; kind: "mobile" | "landline"; ring?: boolean; note?: string };
const OPERATORS: Operator[] = [
  { id: "ee", name: "EE", kind: "mobile", ring: true },
  { id: "o2", name: "O2", kind: "mobile", ring: true },
  { id: "vodafone", name: "Vodafone", kind: "mobile", ring: true },
  { id: "three", name: "Three", kind: "mobile", ring: true },
  { id: "giffgaff", name: "giffgaff / Tesco / Sky Mobile / other UK mobile", kind: "mobile", ring: true },
  { id: "bt", name: "BT landline / Digital Voice", kind: "landline", note: "Call Diversion must be enabled on the line (free on most BT plans)." },
  { id: "virgin", name: "Virgin Media", kind: "landline" },
  { id: "sky", name: "Sky Talk", kind: "landline" },
  { id: "talktalk", name: "TalkTalk", kind: "landline", note: "Enable Call Divert in My Account first." },
  { id: "other", name: "Other UK landline", kind: "landline" },
];
type Scenario = "all" | "no_answer" | "busy" | "unreachable" | "cancel";
const SCENARIOS: [Scenario, string, string][] = [
  ["no_answer", "When I don't answer", "You still answer calls yourself; the assistant picks up when you can't."],
  ["all", "All calls", "Every call goes straight to the assistant."],
  ["busy", "When I'm on another call", "Engaged callers reach the assistant instead of a busy tone."],
  ["unreachable", "When my phone is off / no signal", "Mobile only."],
  ["cancel", "Cancel all diverts", "Turns every divert off."],
];
const RING_SECONDS = [5, 10, 15, 20, 25, 30];

/** GSM / BT supplementary-service codes: mobiles use the ** / ## form, landlines the single * / # form. */
function divertCodes(op: Operator, scenario: Scenario, number: string, ring: number): string[] {
  const n = number.replace(/^\+44/, "0");
  const m = op.kind === "mobile";
  const on = (code: string, tail = "") => (m ? `**${code}*${n}${tail}#` : `*${code}*${n}#`);
  switch (scenario) {
    case "all": return [on("21")];
    case "no_answer": return [on("61", m && op.ring ? `*11*${ring}` : "")];
    case "busy": return [on("67")];
    case "unreachable": return m ? [on("62")] : [];
    case "cancel": return m ? ["##002#"] : ["#21#", "#61#", "#67#"];
  }
}

function DivertCodes({ numbers }: { numbers: TenantNumber[] }) {
  const live = numbers.filter((n) => n.status !== "failed");
  const [operator, setOperator] = useState(OPERATORS[0].id);
  const [scenario, setScenario] = useState<Scenario>("no_answer");
  const [ring, setRing] = useState(20);
  const [target, setTarget] = useState(live[0]?.e164 ?? "");
  const [copied, setCopied] = useState<string | null>(null);
  const op = OPERATORS.find((o) => o.id === operator) ?? OPERATORS[0];
  const e164 = live.some((n) => n.e164 === target) ? target : live[0]?.e164 ?? "";
  const codes = divertCodes(op, scenario, e164, ring);
  const copy = async (c: string) => {
    try { await navigator.clipboard.writeText(c); setCopied(c); setTimeout(() => setCopied(null), 2000); } catch { /* clipboard unavailable */ }
  };
  return (
    <div className="card form" style={{ marginTop: "1rem" }}>
      <h3 style={{ margin: 0 }}>Get your divert code</h3>
      <p className="small muted">Pick your provider and when the assistant should answer; dial the code from the phone you&apos;re diverting. Mobile codes work on any UK network.</p>
      <div className="two">
        <label>Your provider
          <select value={operator} onChange={(e) => setOperator(e.target.value)}>
            <optgroup label="Mobile">{OPERATORS.filter((o) => o.kind === "mobile").map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}</optgroup>
            <optgroup label="Landline">{OPERATORS.filter((o) => o.kind === "landline").map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}</optgroup>
          </select>
        </label>
        <label>Divert calls…
          <select value={scenario} onChange={(e) => setScenario(e.target.value as Scenario)}>
            {SCENARIOS.filter(([s]) => s !== "unreachable" || op.kind === "mobile").map(([s, l]) => <option key={s} value={s}>{l}</option>)}
          </select>
        </label>
        {live.length > 1 && (
          <label>To ParlioTec number
            <select value={e164} onChange={(e) => setTarget(e.target.value)}>{live.map((n) => <option key={n.id} value={n.e164}>{prettyUk(n.e164)}{n.label ? ` · ${n.label}` : ""}</option>)}</select>
          </label>
        )}
        {scenario === "no_answer" && op.kind === "mobile" && (
          <label>Ring for
            <select value={ring} onChange={(e) => setRing(Number(e.target.value))}>{RING_SECONDS.map((s) => <option key={s} value={s}>{s} seconds</option>)}</select>
          </label>
        )}
      </div>
      <p className="small muted">{SCENARIOS.find(([s]) => s === scenario)?.[2]}{scenario === "no_answer" && op.kind === "landline" ? " Landlines ring for about 15 seconds before diverting; change this in your provider's account settings." : ""}</p>
      {codes.map((c) => (
        <div key={c} className="row" style={{ alignItems: "center", gap: 12 }}>
          <code style={{ fontSize: "1.4rem", fontWeight: 600, letterSpacing: ".05em" }}>{c}</code>
          <button type="button" onClick={() => copy(c)}>{copied === c ? "Copied" : "Copy"}</button>
          <a className="btn small" href={`tel:${encodeURIComponent(c)}`}>Dial on this phone</a>
        </div>
      ))}
      <p className="small muted">
        {op.note ? `${op.note} ` : ""}Test it: ring your own number from another phone{scenario === "no_answer" ? " and don't answer" : ""} — the assistant should pick up. To undo, choose &ldquo;Cancel all diverts&rdquo;.
        {op.kind === "landline" ? " Some providers (VoIP, Teams, PBX) set diverts in their app or portal instead of by dial code." : ""}
      </p>
    </div>
  );
}

/** No ParlioTec number yet: the first thing a new tenant needs, so order it right here. */
function GetNumber({ tenant, assistants, canManage, onProvisioned }: { tenant: string; assistants: Assistant[]; canManage: boolean; onProvisioned: (n: TenantNumber) => void }) {
  const [err, setErr] = useState<string | null>(null);
  return (
    <div className="section" style={{ borderColor: "var(--accent)" }}>
      <h2>Step 1 — Get your ParlioTec number</h2>
      <p className="hint">
        Your assistant answers calls on a ParlioTec number. You don&apos;t have one yet, so this is the first step: pick the area code your customers
        expect to see, then click a number to order it. It&apos;s usually live within minutes and we&apos;ll email you when it is. Your first number is included in every plan.
      </p>
      {canManage ? (
        <NumberPicker tenant={tenant} assistants={assistants} onProvisioned={onProvisioned} onError={setErr} compact />
      ) : (
        <p className="small">Only an owner or admin can order a number — ask them to open this page.</p>
      )}
      {err && <p className="small" style={{ color: "var(--bad, #c33)", marginTop: "0.6rem" }}>{err}</p>}
      <p className="small muted" style={{ marginTop: "1rem" }}>
        <b>Step 2</b> — once the number is live, divert your existing line to it (we&apos;ll show you the exact dial code for your provider), or skip the number and connect a PBX / SIP account below instead.
      </p>
    </div>
  );
}

/** Where to go once the number is live and the divert is in place. */
function NextStep({ tenant, numbers }: { tenant: string; numbers: TenantNumber[] }) {
  const [checklist, setChecklist] = useState<SetupChecklist | null>(null);
  const live = numbers.some((n) => n.status === "active");
  useEffect(() => {
    fetchChecklist(tenant).then((c) => c && setChecklist(c));
  }, [tenant, live]);
  const next = checklist?.next_step && checklist.next_step.key !== "number" ? checklist.next_step : null;
  return (
    <div className="card" style={{ marginTop: "1rem", borderColor: "var(--accent)" }}>
      <h3 style={{ margin: 0 }}>Step 3 — What&apos;s next</h3>
      <p className="small muted">
        {live
          ? "Once your divert is set and your test call reached the assistant, this step is done."
          : "Your number is still activating. Set the divert once we email you that it's live, then test it."}
        {checklist ? ` You've completed ${checklist.completed} of ${checklist.total} setup steps.` : ""}
      </p>
      <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
        {next && <Link className="btn primary small" href={next.href}>Next: {next.title}</Link>}
        <Link className="btn small" href="/setup">Back to setup checklist</Link>
        <Link className="btn small" href="/calls">See my calls</Link>
      </div>
    </div>
  );
}

/** Forwarding mode: the ParlioTec number(s) the customer diverts their existing line to. */
function DivertTo({ tenant, numbers, asstName }: { tenant: string; numbers: TenantNumber[]; asstName: (id: string) => string }) {
  const [copied, setCopied] = useState<string | null>(null);
  const copy = async (e164: string) => {
    try { await navigator.clipboard.writeText(e164); setCopied(e164); setTimeout(() => setCopied(null), 2000); } catch { /* clipboard unavailable */ }
  };
  return (
    <div className="section" style={{ borderColor: "var(--accent)" }}>
      <h2>Step 2 — Divert your calls to this number</h2>
      <p className="hint">Set a divert (always, or on no-answer / busy) from your existing landline or mobile to your ParlioTec number below. Microsoft Teams, VoIP and PBX instructions are on the <Link href="/launch">Launch guide</Link>.</p>
      <div className="grid">
        {numbers.map((n) => (
          <div key={n.id} className="card">
            <div style={{ fontSize: "1.6rem", fontWeight: 600, letterSpacing: ".02em" }}>{prettyUk(n.e164)}</div>
            <div className="small muted"><code>{n.e164}</code> · answered by {asstName(n.assistant_id)}{n.label ? ` · ${n.label}` : ""}</div>
            {n.status === "pending" && <p className="small" style={{ marginTop: 6 }}><span className="pill warn">Activating…</span> The carrier is completing its regulatory check - usually minutes, occasionally a few hours. This page updates itself when it&apos;s live; we&apos;ll email you too.</p>}
            {n.status === "active" && <p className="small" style={{ marginTop: 6 }}><span className="pill ok">Live</span> Ready to take calls.</p>}
            {n.status === "failed" && <p className="small" style={{ marginTop: 6 }}><span className="pill bad">Needs attention</span> The carrier declined this number; we&apos;re arranging a replacement.</p>}
            <button type="button" style={{ marginTop: 8 }} onClick={() => copy(n.e164)}>{copied === n.e164 ? "Copied" : "Copy number"}</button>
          </div>
        ))}
      </div>
      {numbers.some((n) => n.status !== "failed") && <DivertCodes numbers={numbers} />}
      <NextStep tenant={tenant} numbers={numbers} />
      <p className="small muted" style={{ marginTop: "0.8rem" }}>Need another number or want to release one? <Link href="/billing?tab=numbers">Billing → Numbers</Link>.</p>
    </div>
  );
}

export default function Telephony({ tenant, canManage, trunks: initial, guides, assistants, numbers: initialNumbers }: { tenant: string; canManage: boolean; trunks: SipTrunk[]; guides: ProviderGuide[]; assistants: Assistant[]; numbers: TenantNumber[] }) {
  const [trunks, setTrunks] = useState(initial);
  const [numbers, setNumbers] = useState(initialNumbers);
  const [creds, setCreds] = useState<IssuedCredentials | null>(null);
  const [test, setTest] = useState<Record<string, TestCallResult>>({});
  const [msg, setMsg] = useState<string | null>(null);
  const [form, setForm] = useState<Form | null>(null);
  const [guide, setGuide] = useState<string | null>(null);
  const q = `?tenant_id=${tenant}`;
  const asstName = (id: string) => assistants.find((a) => a.assistant_id === id)?.name ?? id;
  const pending = numbers.some((n) => n.status === "pending");

  useEffect(() => {
    if (!pending) return;
    const tick = async () => {
      const ns = await fetchNumbers(tenant);
      if (ns) setNumbers(ns);
    };
    const id = setInterval(tick, 30_000);
    return () => clearInterval(id);
  }, [pending, tenant]);

  const refresh = async (t: SipTrunk) => {
    const upd = await post<SipTrunk>(`/v1/telephony/trunks/${t.id}/refresh${q}`);
    if (upd) setTrunks((ts) => ts.map((x) => (x.id === upd.id ? upd : x)));
  };
  const testCall = async (t: SipTrunk) => {
    const r = await post<TestCallResult>(`/v1/telephony/trunks/${t.id}/test-call${q}`, { to: t.ddis[0]?.e164 ?? null });
    if (r) setTest((m) => ({ ...m, [t.id]: r }));
    await refresh(t);
  };
  const rotate = async (t: SipTrunk) => {
    if (!confirm("Issue new PBX credentials? The old password stops working immediately.")) return;
    const c = await post<IssuedCredentials>(`/v1/telephony/trunks/${t.id}/rotate${q}`);
    if (c) setCreds(c); else setMsg("Rotation refused");
  };
  const remove = async (t: SipTrunk) => {
    if (!confirm(`Remove ${t.name}? Calls to its DDIs will stop reaching the assistant.`)) return;
    if (await del(`/v1/telephony/trunks/${t.id}${q}`)) setTrunks((ts) => ts.filter((x) => x.id !== t.id));
  };
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form) return;
    const body = {
      ...form,
      provider_preset: form.provider_preset || null,
      pbx_address: form.pbx_address || null,
      allowed_ips: form.allowed_ips.split(/[\s,]+/).filter(Boolean),
      registrar: form.registrar || null,
      username: form.username || null,
      auth_username: form.auth_username || null,
      outbound_proxy: form.outbound_proxy || null,
      password: form.password || null,
      ddis: form.ddis.filter((d) => d.e164.trim()).map((d) => ({ ...d, department: d.department || null, label: d.label || null })),
    };
    const r = await request<TrunkView>(`/v1/telephony/trunks${q}`, { method: "POST", body: JSON.stringify(body) });
    if (!r.ok) return setMsg(`Could not save: ${r.error}`);
    setTrunks((ts) => [...ts, r.data.trunk]); setCreds(r.data.credentials); setForm(null); setMsg("Trunk saved.");
  };
  const pick = (mode: TrunkMode, presetId?: string) => {
    const f = empty(mode, assistants[0]?.assistant_id ?? "");
    const g = guides.find((x) => x.id === presetId);
    if (g) {
      f.provider_preset = g.id;
      const d = g.defaults;
      if (typeof d.registrar === "string") f.registrar = d.registrar;
      if (typeof d.outbound_proxy === "string") f.outbound_proxy = d.outbound_proxy;
      if (d.transport === "udp" || d.transport === "tcp" || d.transport === "tls") f.transport = d.transport;
      if (typeof d.dtmf === "string") f.dtmf = d.dtmf;
    }
    setForm(f); setGuide(presetId ?? null);
  };
  const setDdi = (i: number, patch: Partial<DdiRoute>) => setForm((f) => f && { ...f, ddis: f.ddis.map((d, j) => (j === i ? { ...d, ...patch } : d)) });
  const modeGuides = form ? guides.filter((g) => g.mode === form.mode) : [];
  const activeGuide = guides.find((g) => g.id === guide);
  const forwarding = trunks.some((t) => t.mode === "forward") || form?.mode === "forward" || (!trunks.length && !form);

  return (
    <>
      {msg && <p className="small" style={{ color: "var(--accent)" }}>{msg}</p>}
      {forwarding && (numbers.length
        ? <DivertTo tenant={tenant} numbers={numbers} asstName={asstName} />
        : <GetNumber tenant={tenant} assistants={assistants} canManage={canManage} onProvisioned={(n) => { setNumbers((ns) => [...ns, n]); setMsg(provisionedMessage(n, asstName(n.assistant_id))); }} />)}
      {creds && (
        <div className="section" style={{ borderColor: "var(--accent)" }}>
          <h2>PBX credentials — copy now, shown once</h2>
          <dl className="kv">
            <dt>SIP domain / registrar</dt><dd><code>{creds.sip_domain}</code></dd>
            <dt>Username</dt><dd><code>{creds.username}</code></dd>
            <dt>Password</dt><dd><code>{creds.password}</code></dd>
            <dt>Transport</dt><dd>{creds.transport.toUpperCase()} · codecs {creds.codecs.join(", ")} · DTMF {creds.dtmf}</dd>
          </dl>
          <button onClick={() => setCreds(null)}>I have saved these</button>
        </div>
      )}

      <div className="section">
        <h2>Your connections</h2>
        <p className="hint">How calls reach the assistant. Registration state and the test call are simulated until the live SIP edge is enabled for your account.</p>
        <table>
          <thead><tr><th>Name</th><th>Mode</th><th>Endpoint</th><th>DDIs</th><th>Calls</th><th>Registration</th><th>Status</th><th></th></tr></thead>
          <tbody>
            {trunks.map((t) => (
              <tr key={t.id}>
                <td>{t.name}{t.provider_preset && <div className="small muted">{t.provider_preset}</div>}</td>
                <td><span className="pill">{MODES.find((m) => m.id === t.mode)?.title.split(" ")[0] ?? t.mode}</span></td>
                <td className="small">{t.mode === "pbx" ? `${t.sip_username}@${t.sip_domain}` : t.mode === "byo_register" ? `${t.username}@${t.registrar}` : "forwarding"}<div className="muted">{t.transport.toUpperCase()}{t.srtp ? "+SRTP" : ""} · {t.codecs.join("/")} · {t.dtmf}</div></td>
                <td className="small">{t.ddis.map((d) => <div key={d.id ?? d.e164}>{d.e164} → {asstName(d.assistant_id)}{d.department ? ` (${d.department})` : ""}{d.when !== "always" ? <span className="muted"> · {WHEN.find(([w]) => w === d.when)?.[1]}</span> : null}</div>)}{!t.ddis.length && <span className="muted">none</span>}</td>
                <td className="small">max {t.max_concurrent_calls}</td>
                <td>{regPill(t)}{t.registration.last_seen_at && <div className="small muted">{when(t.registration.last_seen_at)}</div>}</td>
                <td><span className={`pill ${t.status === "active" ? "ok" : t.status === "error" ? "bad" : "warn"}`}>{humanize(t.status)}</span>{t.last_error && <div className="small muted">{t.last_error}</div>}
                  {test[t.id] && <div className="small" style={{ marginTop: 4 }}>Test call: <span className={`pill ${test[t.id].ok ? "ok" : "bad"}`}>{test[t.id].outcome}</span>{test[t.id].simulated && <span className="muted"> (simulated)</span>}{test[t.id].detail && <div className="muted">{test[t.id].detail}</div>}</div>}
                </td>
                <td className="small">
                  <button onClick={() => refresh(t)}>Refresh</button>{" "}
                  {canManage && <><button onClick={() => testCall(t)}>Test call</button>{" "}{t.mode === "pbx" && <><button onClick={() => rotate(t)}>Rotate creds</button>{" "}</>}<button onClick={() => remove(t)}>Remove</button></>}
                </td>
              </tr>
            ))}
            {!trunks.length && <tr><td colSpan={8} className="muted">No connection yet — pick a mode below.</td></tr>}
          </tbody>
        </table>
      </div>

      {canManage && !form && (
        <div className="grid">
          {MODES.map((m) => (
            <div key={m.id} className="card">
              <h2>{m.title}</h2>
              <p className="small muted">{m.blurb}</p>
              <div className="chips" style={{ marginBottom: 8 }}>
                {guides.filter((g) => g.mode === m.id).map((g) => <a key={g.id} onClick={() => pick(m.id, g.id)}>{g.name}</a>)}
              </div>
              <button className="primary" onClick={() => pick(m.id)}>Set up</button>
            </div>
          ))}
        </div>
      )}

      {form && (
        <form className="section" onSubmit={save}>
          <h2>{MODES.find((m) => m.id === form.mode)?.title}</h2>
          {modeGuides.length > 0 && (
            <div className="chips" style={{ marginBottom: 10 }}>
              {modeGuides.map((g) => <a key={g.id} className={guide === g.id ? "active" : ""} onClick={() => pick(form.mode, g.id)}>{g.name}</a>)}
            </div>
          )}
          {activeGuide && (
            <div className="card" style={{ marginBottom: 12 }}>
              <p className="small">{activeGuide.summary}</p>
              <ol className="small">{activeGuide.steps.map((s, i) => <li key={i}>{s}</li>)}</ol>
              {activeGuide.quirks.length > 0 && <ul className="small muted">{activeGuide.quirks.map((s, i) => <li key={i}>{s}</li>)}</ul>}
            </div>
          )}
          <div className="grid">
            <label>Name<input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required /></label>
            <label>Max concurrent calls<input type="number" min={1} max={500} value={form.max_concurrent_calls} onChange={(e) => setForm({ ...form, max_concurrent_calls: Number(e.target.value) })} /></label>
            {form.mode === "pbx" && (
              <>
                <label>PBX address (for extension transfers)<input value={form.pbx_address} onChange={(e) => setForm({ ...form, pbx_address: e.target.value })} placeholder="pbx.example.co.uk:5060" /></label>
                <label>Allowed source IPs (optional)<input value={form.allowed_ips} onChange={(e) => setForm({ ...form, allowed_ips: e.target.value })} placeholder="203.0.113.10, 203.0.113.11" /></label>
              </>
            )}
            {form.mode === "byo_register" && (
              <>
                <label>Registrar<input value={form.registrar} onChange={(e) => setForm({ ...form, registrar: e.target.value })} placeholder="sip.voipfone.net" required /></label>
                <label>Username / extension<input value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} required /></label>
                <label>Auth username (if different)<input value={form.auth_username} onChange={(e) => setForm({ ...form, auth_username: e.target.value })} /></label>
                <label>Password<input type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} required autoComplete="new-password" /></label>
                <label>Outbound proxy (optional)<input value={form.outbound_proxy} onChange={(e) => setForm({ ...form, outbound_proxy: e.target.value })} /></label>
              </>
            )}
            {form.mode !== "forward" && (
              <>
                <label>Transport
                  <select value={form.transport} onChange={(e) => setForm({ ...form, transport: e.target.value as Form["transport"], srtp: e.target.value === "tls" ? form.srtp : false })}>
                    <option value="udp">UDP</option><option value="tcp">TCP</option><option value="tls">TLS</option>
                  </select>
                </label>
                <label>DTMF
                  <select value={form.dtmf} onChange={(e) => setForm({ ...form, dtmf: e.target.value })}>
                    <option value="rfc2833">RFC 2833</option><option value="inband">In-band</option><option value="info">SIP INFO</option>
                  </select>
                </label>
                <div>
                  <div className="small muted">Codecs</div>
                  <div className="chips">{CODECS.map((c) => <a key={c} className={form.codecs.includes(c) ? "active" : ""} onClick={() => setForm({ ...form, codecs: form.codecs.includes(c) ? form.codecs.filter((x) => x !== c) : [...form.codecs, c] })}>{c}</a>)}</div>
                  <label className="small check"><input type="checkbox" checked={form.srtp} disabled={form.transport !== "tls"} onChange={(e) => setForm({ ...form, srtp: e.target.checked })} /> SRTP (needs TLS)</label>
                </div>
              </>
            )}
          </div>
          <h2 style={{ marginTop: 8 }}>Numbers (DDIs) and routing</h2>
          <p className="hint">Each inbound number goes to an assistant, optionally a department, and can ring the AI always, only out of hours, or when staff don&apos;t answer.</p>
          <table>
            <thead><tr><th>Number (E.164)</th><th>Assistant</th><th>Department</th><th>When</th><th>Label</th><th></th></tr></thead>
            <tbody>
              {form.ddis.map((d, i) => (
                <tr key={i}>
                  <td><input value={d.e164} onChange={(e) => setDdi(i, { e164: e.target.value })} placeholder="+442012345678" /></td>
                  <td><select value={d.assistant_id} onChange={(e) => setDdi(i, { assistant_id: e.target.value })}>{assistants.map((a) => <option key={a.assistant_id} value={a.assistant_id}>{a.name}</option>)}</select></td>
                  <td><input value={d.department ?? ""} onChange={(e) => setDdi(i, { department: e.target.value })} placeholder="sales" /></td>
                  <td><select value={d.when} onChange={(e) => setDdi(i, { when: e.target.value as DdiRoute["when"] })}>{WHEN.map(([w, l]) => <option key={w} value={w}>{l}</option>)}</select></td>
                  <td><input value={d.label ?? ""} onChange={(e) => setDdi(i, { label: e.target.value })} placeholder="Main line" /></td>
                  <td><button type="button" onClick={() => setForm({ ...form, ddis: form.ddis.filter((_, j) => j !== i) })}>×</button></td>
                </tr>
              ))}
            </tbody>
          </table>
          <button type="button" onClick={() => setForm({ ...form, ddis: [...form.ddis, { e164: "", assistant_id: assistants[0]?.assistant_id ?? "", department: null, when: "always", label: null }] })}>Add number</button>
          <div style={{ marginTop: 12 }}>
            <button type="submit" className="primary" disabled={!assistants.length}>Save connection</button>{" "}
            <button type="button" onClick={() => setForm(null)}>Cancel</button>
            {!assistants.length && <span className="muted small" style={{ marginLeft: 8 }}>Create an assistant first.</span>}
          </div>
        </form>
      )}
    </>
  );
}
