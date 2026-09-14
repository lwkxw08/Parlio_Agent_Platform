"use client";

import { useState } from "react";
import {
  type Assistant,
  type BusinessRule,
  type Faq,
  type RequiredField,
  type SmsScenario,
  type VersionSummary,
  fetchSuggestedFaqs,
  fetchVersions,
  post,
  put,
  when,
} from "@/lib/api";
import FaqImport from "./faq-import";

const TABS = ["persona", "business", "hours", "rules", "faqs", "fields", "sms", "languages", "recording", "blocked", "afterhours", "versions"] as const;
type Tab = (typeof TABS)[number];
const LABELS: Record<Tab, string> = {
  persona: "Persona & voice", business: "Business", hours: "Hours", rules: "Rules", faqs: "FAQs", fields: "Required fields",
  sms: "SMS", languages: "Languages", recording: "Recording", blocked: "Blocked numbers", afterhours: "After hours", versions: "Versions",
};
const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
const VOICES: Record<string, { id: string; label: string }[]> = {
  cartesia: [{ id: "f786b574-daa5-4673-aa0c-cbe3e8534c02", label: "Parlio default (British, warm)" }],
  elevenlabs: [],
};
const SMS_TRIGGERS = ["after_call", "missed_call", "booking_link", "address", "payment_link", "ticket_confirmation", "custom"];
const LANGS = [["en", "English"], ["cy", "Welsh"], ["pl", "Polish"], ["ur", "Urdu"], ["pa", "Punjabi"], ["bn", "Bengali"], ["fr", "French"], ["es", "Spanish"], ["de", "German"], ["it", "Italian"], ["pt", "Portuguese"], ["ar", "Arabic"], ["zh", "Mandarin"], ["hi", "Hindi"]];

type Props = { initial: Assistant; versions: VersionSummary[]; requiredFields: RequiredField[] };

export default function Studio({ initial, versions: initialVersions, requiredFields }: Props) {
  const [cfg, setCfg] = useState<Assistant>(initial);
  const [fields, setFields] = useState<RequiredField[]>(requiredFields);
  const [versions, setVersions] = useState(initialVersions);
  const [tab, setTab] = useState<Tab>("persona");
  const [dirty, setDirty] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [suggested, setSuggested] = useState<Faq[] | null>(null);
  const [bulkSms, setBulkSms] = useState("");
  const [bulkBlocked, setBulkBlocked] = useState("");

  const upd = (p: Partial<Assistant>) => { setCfg((c) => ({ ...c, ...p })); setDirty(true); };
  const flash = (m: string) => { setToast(m); setTimeout(() => setToast(null), 2500); };

  const save = async () => {
    const r = await put<Assistant>(`/v1/assistants/${cfg.assistant_id}`, { config: cfg, numbers: [] });
    if (!r.ok) return flash(`Save failed: ${r.error}`);
    const f = await put<RequiredField[]>(`/v1/assistants/${cfg.assistant_id}/required-fields`, fields);
    if (!f.ok) return flash(`Fields save failed: ${f.error}`);
    setCfg(r.data);
    setDirty(false);
    setVersions((await fetchVersions(cfg.assistant_id)) ?? versions);
    flash(`Saved as version ${r.data.assistant_version}`);
  };

  const rollback = async (v: number) => {
    if (!confirm(`Restore version ${v}? This creates a new version with that configuration.`)) return;
    const r = await post<Assistant>(`/v1/assistants/${cfg.assistant_id}/rollback/${v}`);
    if (!r) return flash("Rollback failed");
    setCfg(r); setDirty(false);
    setVersions((await fetchVersions(cfg.assistant_id)) ?? versions);
    flash(`Restored version ${v} as version ${r.assistant_version}`);
  };

  const listEdit = <T,>(key: "rules" | "faqs" | "sms_scenarios", i: number, patch: Partial<T>) => {
    const arr = [...(cfg[key] as T[])];
    arr[i] = { ...arr[i], ...patch };
    upd({ [key]: arr } as Partial<Assistant>);
  };
  const listRemove = (key: "rules" | "faqs" | "sms_scenarios", i: number) =>
    upd({ [key]: (cfg[key] as unknown[]).filter((_, j) => j !== i) } as Partial<Assistant>);

  const faqCategories = Array.from(new Set(cfg.faqs.map((f) => f.category))).sort();

  return (
    <>
      <div className="tabs">
        {TABS.map((t) => <button key={t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>{LABELS[t]}</button>)}
        <span style={{ marginLeft: "auto", display: "flex", gap: 8, alignItems: "center" }}>
          {dirty && <span className="muted small">unsaved changes</span>}
          <button className="primary" disabled={!dirty} onClick={save}>Save new version</button>
        </span>
      </div>

      {tab === "persona" && (
        <div className="section form">
          <div className="two">
            <label>Assistant name <input value={cfg.name} onChange={(e) => upd({ name: e.target.value })} /></label>
            <label>Business name (as spoken) <input value={cfg.business_name} onChange={(e) => upd({ business_name: e.target.value })} /></label>
          </div>
          <label>Greeting <input value={cfg.greeting} onChange={(e) => upd({ greeting: e.target.value })} /></label>
          <div className="two">
            <label>Tone
              <select value={cfg.persona.tone} onChange={(e) => upd({ persona: { ...cfg.persona, tone: e.target.value } })}>
                {["friendly and professional", "warm and reassuring", "upbeat and energetic", "calm and precise", "formal and courteous"].map((t) => <option key={t}>{t}</option>)}
              </select>
            </label>
            <label>Formality
              <select value={cfg.persona.formality} onChange={(e) => upd({ persona: { ...cfg.persona, formality: e.target.value } })}>
                {["casual", "conversational", "formal"].map((t) => <option key={t}>{t}</option>)}
              </select>
            </label>
            <label>Pace
              <select value={cfg.persona.pace} onChange={(e) => upd({ persona: { ...cfg.persona, pace: e.target.value } })}>
                {["slow", "normal", "brisk"].map((t) => <option key={t}>{t}</option>)}
              </select>
            </label>
            <label>Voice provider
              <select value={cfg.voice.provider} onChange={(e) => upd({ voice: { ...cfg.voice, provider: e.target.value, voice_id: VOICES[e.target.value]?.[0]?.id ?? cfg.voice.voice_id } })}>
                <option value="cartesia">Cartesia Sonic (lowest latency)</option>
                <option value="elevenlabs">ElevenLabs Flash</option>
              </select>
            </label>
            <label>Voice
              <select value={cfg.voice.voice_id} onChange={(e) => upd({ voice: { ...cfg.voice, voice_id: e.target.value } })}>
                {(VOICES[cfg.voice.provider] ?? []).map((v) => <option key={v.id} value={v.id}>{v.label}</option>)}
                {!(VOICES[cfg.voice.provider] ?? []).some((v) => v.id === cfg.voice.voice_id) && <option value={cfg.voice.voice_id}>Custom: {cfg.voice.voice_id}</option>}
              </select>
            </label>
            <label>Voice ID (paste from the provider&apos;s voice library) <input value={cfg.voice.voice_id} onChange={(e) => upd({ voice: { ...cfg.voice, voice_id: e.target.value } })} /></label>
          </div>
          <label>Speaking speed ({cfg.voice.speed ?? 1}×)
            <input type="range" min={0.8} max={1.2} step={0.05} value={cfg.voice.speed ?? 1} onChange={(e) => upd({ voice: { ...cfg.voice, speed: Number(e.target.value) } })} />
          </label>
          <label>Extra persona guidance <textarea value={cfg.persona.extra} placeholder="e.g. Always mention we are family-run. Never quote prices over the phone." onChange={(e) => upd({ persona: { ...cfg.persona, extra: e.target.value } })} /></label>
          <label>Core instructions (advanced) <textarea value={cfg.instructions} onChange={(e) => upd({ instructions: e.target.value })} /></label>
        </div>
      )}

      {tab === "business" && (
        <div className="section form">
          <label>Description <textarea value={cfg.business.description} onChange={(e) => upd({ business: { ...cfg.business, description: e.target.value } })} /></label>
          <div className="two">
            <label>Website <input value={cfg.business.website ?? ""} onChange={(e) => upd({ business: { ...cfg.business, website: e.target.value || null } })} /></label>
            <label>Phone <input value={cfg.business.phone ?? ""} onChange={(e) => upd({ business: { ...cfg.business, phone: e.target.value || null } })} /></label>
            <label>Email <input value={cfg.business.email ?? ""} onChange={(e) => upd({ business: { ...cfg.business, email: e.target.value || null } })} /></label>
            <label>Address <input value={cfg.business.address ?? ""} onChange={(e) => upd({ business: { ...cfg.business, address: e.target.value || null } })} /></label>
          </div>
          <label>Services (one per line)
            <textarea value={cfg.business.services.join("\n")} onChange={(e) => upd({ business: { ...cfg.business, services: e.target.value.split("\n").map((s) => s.trim()).filter(Boolean) } })} />
          </label>
        </div>
      )}

      {tab === "hours" && (
        <div className="section form">
          <div className="two">
            <label>Timezone
              <select value={cfg.hours.timezone} onChange={(e) => upd({ hours: { ...cfg.hours, timezone: e.target.value } })}>
                {["Europe/London", "Europe/Dublin", "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles"].map((z) => <option key={z}>{z}</option>)}
              </select>
            </label>
            <label style={{ flexDirection: "row", alignItems: "center", gap: 8, marginTop: "1.4rem" }}>
              <input type="checkbox" checked={cfg.hours.always} onChange={(e) => upd({ hours: { ...cfg.hours, always: e.target.checked } })} /> Open 24/7
            </label>
          </div>
          {!cfg.hours.always && (
            <div className="hours-grid">
              {DAYS.map((d) => {
                const h = cfg.hours.hours[d];
                const set = (v: { open: string; close: string } | null) => {
                  const hours = { ...cfg.hours.hours };
                  if (v) hours[d] = v; else delete hours[d];
                  upd({ hours: { ...cfg.hours, hours } });
                };
                return (
                  <div key={d} style={{ display: "contents" }}>
                    <span style={{ textTransform: "capitalize" }}>{d}</span>
                    <input type="time" disabled={!h} value={h?.open.slice(0, 5) ?? ""} onChange={(e) => h && set({ ...h, open: e.target.value })} />
                    <input type="time" disabled={!h} value={h?.close.slice(0, 5) ?? ""} onChange={(e) => h && set({ ...h, close: e.target.value })} />
                    <button type="button" className="ghost" onClick={() => set(h ? null : { open: "09:00", close: "17:30" })}>{h ? "Closed" : "Open"}</button>
                  </div>
                );
              })}
            </div>
          )}
          <p className="hint">Outside these hours the assistant follows the after-hours behaviour and tells callers when you reopen.</p>
        </div>
      )}

      {tab === "rules" && (
        <div className="section">
          <h2>Key business rules</h2>
          <p className="hint">Short, unambiguous instructions the assistant must always follow, e.g. “Never confirm a booking without a phone number”.</p>
          {cfg.rules.map((r, i) => (
            <div className="list-row" key={r.id ?? i}>
              <input value={r.name} placeholder="Rule name" onChange={(e) => listEdit<BusinessRule>("rules", i, { name: e.target.value })} />
              <textarea value={r.instruction} placeholder="Instruction" onChange={(e) => listEdit<BusinessRule>("rules", i, { instruction: e.target.value })} />
              <span>
                <label className="small"><input type="checkbox" checked={r.enabled} onChange={(e) => listEdit<BusinessRule>("rules", i, { enabled: e.target.checked })} /> on</label>{" "}
                <button className="danger" onClick={() => listRemove("rules", i)}>✕</button>
              </span>
            </div>
          ))}
          <button className="ghost" onClick={() => upd({ rules: [...cfg.rules, { name: "", instruction: "", enabled: true }] })}>+ Add rule</button>
        </div>
      )}

      {tab === "faqs" && (
        <div className="section">
          <h2>FAQs {faqCategories.length > 0 && <span className="muted small">— {faqCategories.join(", ")}</span>}</h2>
          <p className="hint">Grouped by category; disabled FAQs are kept but not used on calls.</p>
          {faqCategories.map((cat) => (
            <div key={cat}>
              <h3 className="small muted" style={{ textTransform: "uppercase", letterSpacing: ".05em", margin: "1rem 0 0" }}>{cat}</h3>
              {cfg.faqs.map((f, i) => f.category === cat && (
                <div className="list-row" key={f.id ?? i}>
                  <div>
                    <input value={f.question} placeholder="Question" onChange={(e) => listEdit<Faq>("faqs", i, { question: e.target.value })} />
                    <input value={f.category} style={{ marginTop: 4 }} placeholder="Category" onChange={(e) => listEdit<Faq>("faqs", i, { category: e.target.value })} />
                  </div>
                  <textarea value={f.answer} placeholder="Answer" onChange={(e) => listEdit<Faq>("faqs", i, { answer: e.target.value })} />
                  <span>
                    <label className="small"><input type="checkbox" checked={f.enabled} onChange={(e) => listEdit<Faq>("faqs", i, { enabled: e.target.checked })} /> on</label>{" "}
                    {f.source !== "manual" && <span className="pill">{f.source}</span>}{" "}
                    <button className="danger" onClick={() => listRemove("faqs", i)}>✕</button>
                  </span>
                </div>
              ))}
            </div>
          ))}
          <div style={{ marginTop: "1rem", display: "flex", gap: 8 }}>
            <button className="ghost" onClick={() => upd({ faqs: [...cfg.faqs, { category: "general", question: "", answer: "", enabled: true, source: "manual" }] })}>+ Add FAQ</button>
            <button className="ghost" onClick={async () => setSuggested((await fetchSuggestedFaqs(cfg.assistant_id)) ?? [])}>Suggest from recent calls</button>
          </div>
          {suggested && (
            <div style={{ marginTop: "1rem" }}>
              <h3 className="small">AI-suggested FAQs {suggested.length === 0 && <span className="muted">— nothing new found in recent calls</span>}</h3>
              {suggested.map((s, i) => (
                <div className="list-row" key={i}>
                  <b className="small">{s.question}</b>
                  <span className="small muted">{s.answer || "(write the answer)"}</span>
                  <button className="ghost" onClick={() => { upd({ faqs: [...cfg.faqs, { ...s, source: "suggested", enabled: Boolean(s.answer) }] }); setSuggested(suggested.filter((_, j) => j !== i)); }}>Add</button>
                </div>
              ))}
            </div>
          )}
          <FaqImport assistant={cfg} onApplied={async (next, added) => {
            setCfg(next); setDirty(false);
            setVersions((await fetchVersions(cfg.assistant_id)) ?? versions);
            flash(added ? `Added ${added} FAQ${added === 1 ? "" : "s"} — saved as version ${next.assistant_version}` : "No new FAQs to add");
          }} />
        </div>
      )}

      {tab === "fields" && (
        <div className="section">
          <h2>Information to collect</h2>
          <p className="hint">The assistant gathers these from every caller; missing ones are flagged on the call and in analytics.</p>
          {fields.map((f, i) => (
            <div className="list-row" key={i}>
              <input value={f.name} placeholder="field_name" onChange={(e) => { const a = [...fields]; a[i] = { ...f, name: e.target.value.replace(/\s+/g, "_").toLowerCase() }; setFields(a); setDirty(true); }} />
              <input value={f.description} placeholder="What to ask for, e.g. the caller's postcode" onChange={(e) => { const a = [...fields]; a[i] = { ...f, description: e.target.value }; setFields(a); setDirty(true); }} />
              <span>
                <label className="small"><input type="checkbox" checked={f.required} onChange={(e) => { const a = [...fields]; a[i] = { ...f, required: e.target.checked }; setFields(a); setDirty(true); }} /> required</label>{" "}
                <button className="danger" onClick={() => { setFields(fields.filter((_, j) => j !== i)); setDirty(true); }}>✕</button>
              </span>
            </div>
          ))}
          <button className="ghost" onClick={() => { setFields([...fields, { name: "", description: "", required: true }]); setDirty(true); }}>+ Add field</button>
        </div>
      )}

      {tab === "sms" && (
        <div className="section">
          <h2>SMS scenarios</h2>
          <p className="hint">Texts the assistant can send during or after a call. Placeholders: {"{business_name}"}, {"{caller_name}"}, {"{address}"}, {"{booking_link}"}, {"{ticket_id}"}. Sending goes live with the messaging integration (Phase 5).</p>
          {cfg.sms_scenarios.map((s, i) => (
            <div className="list-row" key={s.id ?? i}>
              <div>
                <input value={s.name} placeholder="Name" onChange={(e) => listEdit<SmsScenario>("sms_scenarios", i, { name: e.target.value })} />
                <select value={s.trigger} style={{ marginTop: 4 }} onChange={(e) => listEdit<SmsScenario>("sms_scenarios", i, { trigger: e.target.value })}>
                  {SMS_TRIGGERS.map((t) => <option key={t} value={t}>{t.replaceAll("_", " ")}</option>)}
                </select>
              </div>
              <textarea value={s.template} placeholder="Message template" maxLength={320} onChange={(e) => listEdit<SmsScenario>("sms_scenarios", i, { template: e.target.value })} />
              <span>
                <label className="small"><input type="checkbox" checked={s.enabled} onChange={(e) => listEdit<SmsScenario>("sms_scenarios", i, { enabled: e.target.checked })} /> on</label>{" "}
                <button className="danger" onClick={() => listRemove("sms_scenarios", i)}>✕</button>
              </span>
            </div>
          ))}
          <div style={{ display: "flex", gap: 8, marginTop: "1rem", flexWrap: "wrap" }}>
            <button className="ghost" onClick={() => upd({ sms_scenarios: [...cfg.sms_scenarios, { trigger: "custom", name: "", template: "", enabled: true }] })}>+ Add scenario</button>
            <button className="ghost" onClick={() => upd({ sms_scenarios: [...cfg.sms_scenarios,
              { trigger: "after_call", name: "Thanks for calling", template: "Thanks for calling {business_name}. We'll be in touch shortly. Reply STOP to opt out.", enabled: true },
              { trigger: "missed_call", name: "Sorry we missed you", template: "Sorry we missed your call to {business_name}. Reply or call back and we'll help straight away.", enabled: true },
              { trigger: "address", name: "Our address", template: "{business_name}: {address}", enabled: true },
              { trigger: "ticket_confirmation", name: "Callback confirmed", template: "Hi {caller_name}, we've logged your request (ref {ticket_id}) and will call you back as agreed.", enabled: true },
            ] })}>Add example scenarios</button>
          </div>
          <details style={{ marginTop: "1rem" }}>
            <summary className="small">Bulk add (one per line: <code>trigger | name | template</code>)</summary>
            <textarea className="form" style={{ width: "100%", minHeight: "6rem", marginTop: 6 }} value={bulkSms} onChange={(e) => setBulkSms(e.target.value)} />
            <button className="ghost" style={{ marginTop: 6 }} onClick={() => {
              const rows = bulkSms.split("\n").map((l) => l.split("|").map((s) => s.trim())).filter((p) => p.length === 3 && p[2]);
              upd({ sms_scenarios: [...cfg.sms_scenarios, ...rows.map(([trigger, name, template]) => ({ trigger: SMS_TRIGGERS.includes(trigger) ? trigger : "custom", name, template, enabled: true }))] });
              setBulkSms("");
            }}>Import</button>
          </details>
        </div>
      )}

      {tab === "languages" && (
        <div className="section form">
          <h2>Languages</h2>
          <p className="hint">The assistant answers in the primary language and switches when a caller speaks another enabled language.</p>
          <label>Primary language
            <select value={cfg.language} onChange={(e) => upd({ language: e.target.value, languages: Array.from(new Set([e.target.value, ...cfg.languages])) })}>
              {LANGS.map(([c, l]) => <option key={c} value={c}>{l}</option>)}
            </select>
          </label>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(160px, 1fr))", gap: 6 }}>
            {LANGS.map(([c, l]) => (
              <label key={c} style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
                <input type="checkbox" disabled={c === cfg.language} checked={cfg.languages.includes(c)} onChange={(e) => upd({ languages: e.target.checked ? [...cfg.languages, c] : cfg.languages.filter((x) => x !== c) })} /> {l}
              </label>
            ))}
          </div>
        </div>
      )}

      {tab === "recording" && (
        <div className="section form">
          <h2>Recording & consent</h2>
          <label style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
            <input type="checkbox" checked={cfg.recording.enabled} onChange={(e) => upd({ recording: { ...cfg.recording, enabled: e.target.checked } })} /> Record calls (dual-channel, stored in your region)
          </label>
          <p className="hint">UK GDPR/PECR: callers must be told calls are recorded. The announcement below is played once at the start of each recorded call, in the caller&apos;s language where available.</p>
          {cfg.languages.map((l) => (
            <label key={l}>Consent announcement ({LANGS.find(([c]) => c === l)?.[1] ?? l})
              <input value={cfg.recording.consent_announcement[l] ?? ""} placeholder={l === "en" ? "This call may be recorded for quality and training purposes." : "Leave blank to use English"} onChange={(e) => upd({ recording: { ...cfg.recording, consent_announcement: { ...cfg.recording.consent_announcement, [l]: e.target.value } } })} />
            </label>
          ))}
        </div>
      )}

      {tab === "blocked" && (
        <div className="section form">
          <h2>Blocked numbers</h2>
          <p className="hint">Calls from these numbers are ended immediately without answering and logged as “blocked”. Use E.164 (e.g. +447700900123).</p>
          <textarea value={bulkBlocked || cfg.blocked_numbers.join("\n")} onChange={(e) => setBulkBlocked(e.target.value)} onBlur={() => { if (bulkBlocked) { upd({ blocked_numbers: Array.from(new Set(bulkBlocked.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean))) }); setBulkBlocked(""); } }} />
          <p className="small muted">{cfg.blocked_numbers.length} blocked</p>
        </div>
      )}

      {tab === "afterhours" && (
        <div className="section form">
          <h2>After-hours & no-answer behaviour</h2>
          <label>When nobody is available
            <select value={cfg.transfer.after_hours} onChange={(e) => upd({ transfer: { ...cfg.transfer, after_hours: e.target.value as Assistant["transfer"]["after_hours"] } })}>
              <option value="ticket">Take a message as a ticket (callback queue)</option>
              <option value="voicemail">Offer voicemail</option>
              <option value="both">Both — ticket plus voicemail</option>
            </select>
          </label>
          <div className="two">
            <label>Transfer mode
              <select value={cfg.transfer.mode} onChange={(e) => upd({ transfer: { ...cfg.transfer, mode: e.target.value as "warm" | "cold" } })}>
                <option value="warm">Warm — brief the human first</option>
                <option value="cold">Cold — blind transfer</option>
              </select>
            </label>
            <label>Ring timeout (s) <input type="number" min={5} max={90} value={cfg.transfer.ring_timeout_s} onChange={(e) => upd({ transfer: { ...cfg.transfer, ring_timeout_s: Number(e.target.value) } })} /></label>
          </div>
          <label>Urgent keywords (comma-separated) — trigger immediate escalation to on-call staff
            <input value={cfg.transfer.urgent_keywords.join(", ")} onChange={(e) => upd({ transfer: { ...cfg.transfer, urgent_keywords: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) } })} />
          </label>
          <p className="hint">Destinations and departments are managed on the <a href="/handoff">Transfers</a> page.</p>
        </div>
      )}

      {tab === "versions" && (
        <div className="section">
          <h2>Version history</h2>
          <p className="hint">Every save creates a new version; live calls always use the latest. Restoring an older version creates a new one, so nothing is lost.</p>
          <table>
            <thead><tr><th>Version</th><th>Saved</th><th>By</th><th>Name</th><th>FAQs</th><th>Rules</th><th>Note</th><th></th></tr></thead>
            <tbody>
              {versions.map((v) => (
                <tr key={v.version}>
                  <td>v{v.version}{v.version === cfg.assistant_version && <span className="pill ok" style={{ marginLeft: 6 }}>live</span>}</td>
                  <td>{when(v.created_at)}</td><td>{v.created_by ?? "—"}</td><td>{v.name}</td><td>{v.faq_count}</td><td>{v.rule_count}</td>
                  <td className="small muted">{v.note ?? ""}</td>
                  <td>{v.version !== cfg.assistant_version && <button className="ghost" onClick={() => rollback(v.version)}>Restore</button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {toast && <div className="toast">{toast}</div>}
    </>
  );
}
