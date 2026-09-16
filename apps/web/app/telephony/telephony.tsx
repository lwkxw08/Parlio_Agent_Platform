"use client";

import { useState } from "react";
import {
  type Assistant,
  type DdiRoute,
  type IssuedCredentials,
  type ProviderGuide,
  type SipTrunk,
  type TestCallResult,
  type TrunkMode,
  type TrunkView,
  del,
  post,
  request,
  when,
} from "@/lib/api";
import { humanize } from "@/app/breakdown";

const MODES: { id: TrunkMode; title: string; blurb: string }[] = [
  { id: "forward", title: "Forward to your Parlio number", blurb: "Keep your provider. Forward calls (always or on no-answer) to the number Parlio gives you. No SIP setup." },
  { id: "pbx", title: "Connect your PBX", blurb: "Parlio issues SIP credentials for 3CX, FreePBX, Gamma Horizon, RingCentral, BT Cloud Voice… Your PBX sends calls to us and extension transfers stay internal." },
  { id: "byo_register", title: "Use your SIP account", blurb: "Give us your provider login (Voipfone, Sipgate, Gamma…). Parlio registers as that account so your DDI rings straight into the assistant and transfers use your caller ID." },
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

export default function Telephony({ tenant, canManage, trunks: initial, guides, assistants }: { tenant: string; canManage: boolean; trunks: SipTrunk[]; guides: ProviderGuide[]; assistants: Assistant[] }) {
  const [trunks, setTrunks] = useState(initial);
  const [creds, setCreds] = useState<IssuedCredentials | null>(null);
  const [test, setTest] = useState<Record<string, TestCallResult>>({});
  const [msg, setMsg] = useState<string | null>(null);
  const [form, setForm] = useState<Form | null>(null);
  const [guide, setGuide] = useState<string | null>(null);
  const q = `?tenant_id=${tenant}`;
  const asstName = (id: string) => assistants.find((a) => a.assistant_id === id)?.name ?? id;

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

  return (
    <>
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
        {msg && <p className="muted small">{msg}</p>}
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
