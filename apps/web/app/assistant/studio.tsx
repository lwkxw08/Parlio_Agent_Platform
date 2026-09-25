"use client";

import { useState } from "react";
import { useHashTab } from "@/app/help";
import {
  type AfterHoursPersona,
  type Assistant,
  type BusinessRule,
  type Holiday,
  type Faq,
  type RegressionCheck,
  type RequiredField,
  type ScreeningMode,
  type SmsScenario,
  type SpeakingStyle,
  type VersionSummary,
  type WebsiteSearchConfig,
  fetchSuggestedFaqs,
  fetchVersions,
  post,
  publishAssistant,
  put,
  when,
} from "@/lib/api";
import AskAi from "./ask-ai";
import FaqImport from "./faq-import";
import VoicePicker from "./voice-picker";
import { humanize } from "@/app/breakdown";
import { SetupNextStep } from "@/app/setup-next-step";

const TABS = ["persona", "speaking", "business", "hours", "rules", "faqs", "fields", "sms", "languages", "recording", "blocked", "afterhours", "versions"] as const;
type Tab = (typeof TABS)[number];
/** Guide anchors (docs/guide/assistant-studio.md headings) → the tab that shows them. */
const ANCHOR_TABS: Record<string, Tab> = {
  "identity-personality": "persona", voice: "persona", "speaking-style": "speaking", interruptions: "speaking", glossary: "speaking",
  "live-website-search": "business", "business-hours": "hours",
  "key-business-rules": "rules", faqs: "faqs", "information-to-collect": "fields", "sms-scenarios": "sms",
  languages: "languages", "recording-consent": "recording", "call-screening": "blocked", "blocked-numbers": "blocked",
  "closed-hours-persona": "afterhours", "no-answer-behaviour": "afterhours", "version-history": "versions", "publish-checks": "versions",
};
const LABELS: Record<Tab, string> = {
  persona: "Persona & voice", speaking: "Speaking style", business: "Business", hours: "Hours", rules: "Rules", faqs: "FAQs", fields: "Required fields",
  sms: "SMS", languages: "Languages", recording: "Recording", blocked: "Screening & blocking", afterhours: "After hours", versions: "Versions",
};
const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
const SMS_TRIGGERS = ["after_call", "missed_call", "booking_link", "address", "payment_link", "ticket_confirmation", "custom"];
const LANGS = [["en", "English"], ["cy", "Welsh"], ["pl", "Polish"], ["ur", "Urdu"], ["pa", "Punjabi"], ["bn", "Bengali"], ["fr", "French"], ["es", "Spanish"], ["de", "German"], ["it", "Italian"], ["pt", "Portuguese"], ["ar", "Arabic"], ["zh", "Mandarin"], ["hi", "Hindi"]];

type Props = { initial: Assistant; versions: VersionSummary[]; requiredFields: RequiredField[] };

export default function Studio({ initial, versions: initialVersions, requiredFields }: Props) {
  const [cfg, setCfg] = useState<Assistant>(initial);
  const [fields, setFields] = useState<RequiredField[]>(requiredFields);
  const [versions, setVersions] = useState(initialVersions);
  const [tab, setTab] = useState<Tab>("persona");
  useHashTab(ANCHOR_TABS, setTab);
  const [dirty, setDirty] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [suggested, setSuggested] = useState<Faq[] | null>(null);
  const [bulkSms, setBulkSms] = useState("");
  const [bulkBlocked, setBulkBlocked] = useState("");
  const [bulkAllowed, setBulkAllowed] = useState("");
  const [blocked, setBlocked] = useState<RegressionCheck | null>(null);
  const [saving, setSaving] = useState(false);

  const upd = (p: Partial<Assistant>) => { setCfg((c) => ({ ...c, ...p })); setDirty(true); };
  const flash = (m: string) => { setToast(m); setTimeout(() => setToast(null), 2500); };

  const save = async (force = false) => {
    setSaving(true);
    const r = await publishAssistant(cfg.tenant_id, cfg, force);
    setSaving(false);
    if (!r.ok) return flash(`Save failed: ${r.error}`);
    if (!r.data.config) { setBlocked(r.data.check); return; }
    setBlocked(null);
    const f = await put<RequiredField[]>(`/v1/assistants/${cfg.assistant_id}/required-fields`, fields);
    if (!f.ok) return flash(`Fields save failed: ${f.error}`);
    setCfg(r.data.config);
    setDirty(false);
    setVersions((await fetchVersions(cfg.assistant_id)) ?? versions);
    const c = r.data.check;
    const pack = c.pack_size ? ` · regression pack ${c.candidate_passed}/${c.pack_size} passed` : "";
    flash(`Saved as version ${r.data.config.assistant_version}${pack}`);
  };

  const rollback = async (v: number) => {
    if (!confirm(`Restore version ${v}? This creates a new version with that configuration.`)) return;
    const r = await post<Assistant>(`/v1/assistants/${cfg.assistant_id}/rollback/${v}`);
    if (!r) return flash("Rollback failed");
    setCfg(r); setDirty(false);
    setVersions((await fetchVersions(cfg.assistant_id)) ?? versions);
    flash(`Restored version ${v} as version ${r.assistant_version}`);
  };

  const listEdit = <T,>(key: "rules" | "faqs" | "sms_scenarios" | "glossary", i: number, patch: Partial<T>) => {
    const arr = [...(cfg[key] as T[])];
    arr[i] = { ...arr[i], ...patch };
    upd({ [key]: arr } as Partial<Assistant>);
  };
  const listRemove = (key: "rules" | "faqs" | "sms_scenarios" | "glossary", i: number) =>
    upd({ [key]: (cfg[key] as unknown[]).filter((_, j) => j !== i) } as Partial<Assistant>);

  const faqCategories = Array.from(new Set(cfg.faqs.map((f) => f.category))).sort();

  return (
    <>
      <div className="tabs">
        {TABS.map((t) => <button key={t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>{LABELS[t]}</button>)}
        <span style={{ marginLeft: "auto", display: "flex", gap: 8, alignItems: "center" }}>
          {dirty && <span className="muted small">unsaved changes</span>}
          <button className="primary" disabled={!dirty || saving} onClick={() => save()}>{saving ? "Checking regression pack…" : "Save new version"}</button>
        </span>
      </div>

      {tab === "persona" && (
        <div className="section form">
          <div className="two">
            <label>Assistant name <input value={cfg.name} onChange={(e) => upd({ name: e.target.value })} /></label>
            <label>Business name (as spoken) <input value={cfg.business_name} onChange={(e) => upd({ business_name: e.target.value })} /></label>
          </div>
          <label><span className="row" style={{ alignItems: "center" }}>Greeting <AskAi assistantId={cfg.assistant_id} field="greeting" current={cfg.greeting} website={cfg.business.website} onInsert={(t) => upd({ greeting: t.replace(/\s+/g, " ").trim() })} /></span><input value={cfg.greeting} onChange={(e) => upd({ greeting: e.target.value })} /></label>
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
          </div>
          <h3 style={{ margin: "0.5rem 0 0.25rem" }}>Voice</h3>
          <VoicePicker value={cfg.voice} businessName={cfg.business_name} onChange={(voice) => upd({ voice })} />
          <label>Speaking speed ({cfg.voice.speed ?? 1}×)
            <input type="range" min={0.8} max={1.2} step={0.05} value={cfg.voice.speed ?? 1} onChange={(e) => upd({ voice: { ...cfg.voice, speed: Number(e.target.value) } })} />
          </label>
          <label><span className="row" style={{ alignItems: "center" }}>Extra persona guidance <AskAi assistantId={cfg.assistant_id} field="persona_extra" current={cfg.persona.extra} website={cfg.business.website} onInsert={(t) => upd({ persona: { ...cfg.persona, extra: t } })} /></span><textarea value={cfg.persona.extra} placeholder="e.g. Always mention we are family-run. Never quote prices over the phone." onChange={(e) => upd({ persona: { ...cfg.persona, extra: e.target.value } })} /></label>
          <label><span className="row" style={{ alignItems: "center" }}>Core instructions (advanced) <AskAi assistantId={cfg.assistant_id} field="instructions" current={cfg.instructions} website={cfg.business.website} onInsert={(t) => upd({ instructions: t })} /></span><textarea value={cfg.instructions} onChange={(e) => upd({ instructions: e.target.value })} /></label>
        </div>
      )}

      {tab === "speaking" && (() => {
        const s: SpeakingStyle = cfg.speaking ?? { one_detail_at_a_time: true, digits_individually: true, spell_postcodes: true, summary_per_line: true, confirm_phrase: "is that right?", final_confirm_phrase: "Is all of that correct?", extra_rules: [] };
        const set = (p: Partial<SpeakingStyle>) => upd({ speaking: { ...s, ...p } });
        const toggles: [keyof SpeakingStyle & ("one_detail_at_a_time" | "digits_individually" | "spell_postcodes" | "summary_per_line"), string, string][] = [
          ["one_detail_at_a_time", "Confirm one detail at a time", "Name, then number, then address — each checked before moving on, never all in one sentence."],
          ["digits_individually", "Phone numbers digit by digit", "\"0 7 9 3 0, 9 3 4, 0 9 8\" rather than \"nine hundred and thirty-four\". Also applied to the spoken audio automatically."],
          ["spell_postcodes", "Spell postcodes letter by letter", "\"M 2 1, 2 D F\"; addresses read slowly one line at a time and unclear postcodes/names spelt back."],
          ["summary_per_line", "Final summary one detail per sentence", "\"Your name is Keith Wilson. Your callback number is … \" with a pause between each, ending with a single confirmation question."],
        ];
        return (
          <div className="section form">
            <p className="muted small">How the assistant reads details back to callers. Pace and voice speed are under Persona &amp; voice.</p>
            {toggles.map(([k, title, help]) => (
              <label key={k} className="row" style={{ alignItems: "flex-start", gap: 10 }}>
                <input type="checkbox" checked={s[k]} onChange={(e) => set({ [k]: e.target.checked } as Partial<SpeakingStyle>)} style={{ marginTop: 4 }} />
                <span><strong>{title}</strong><br /><span className="muted small">{help}</span></span>
              </label>
            ))}
            <div className="two">
              <label>Check phrase after each detail <input value={s.confirm_phrase} onChange={(e) => set({ confirm_phrase: e.target.value })} /></label>
              <label>Final confirmation question <input value={s.final_confirm_phrase} onChange={(e) => set({ final_confirm_phrase: e.target.value })} /></label>
            </div>
            <label>Your own read-back rules (one per line)
              <textarea
                value={s.extra_rules.join("\n")}
                placeholder={"e.g. Read job reference numbers as pairs of digits.\nAlways repeat the appointment date and time back before ending the call."}
                onChange={(e) => set({ extra_rules: e.target.value.split("\n") })}
                onBlur={() => set({ extra_rules: s.extra_rules.map((r) => r.trim()).filter(Boolean) })}
              />
            </label>
            <h2 style={{ marginTop: "1.2rem" }} id="interruptions">Interruptions</h2>
            <label className="row" style={{ alignItems: "flex-start", gap: 10 }}>
              <input type="checkbox" checked={cfg.turn?.allow_interruptions ?? true} onChange={(e) => upd({ turn: { ...cfg.turn, allow_interruptions: e.target.checked } })} style={{ marginTop: 4 }} />
              <span><strong>Let callers interrupt the assistant</strong><br /><span className="muted small">On (recommended): the assistant stops talking as soon as the caller speaks. Off: it finishes each sentence first — useful on noisy lines or where callers talk over hold-style announcements.</span></span>
            </label>
            <h2 style={{ marginTop: "1.2rem" }} id="glossary">Glossary &amp; pronunciations</h2>
            <p className="hint">Brand, product, place and staff names the assistant must recognise and say correctly. “Say as” is how it is spoken (e.g. <i>Saoirse</i> → <i>Seer-sha</i>); “Meaning” tells the assistant what the term is.</p>
            {(cfg.glossary ?? []).map((g, i) => (
              <div key={i} className="row" style={{ gap: 8, alignItems: "center" }}>
                <input placeholder="Term" value={g.term} onChange={(e) => listEdit("glossary", i, { ...g, term: e.target.value })} style={{ flex: 1 }} />
                <input placeholder="Say as (optional)" value={g.say_as} onChange={(e) => listEdit("glossary", i, { ...g, say_as: e.target.value })} style={{ flex: 1 }} />
                <input placeholder="Meaning (optional)" value={g.meaning} onChange={(e) => listEdit("glossary", i, { ...g, meaning: e.target.value })} style={{ flex: 2 }} />
                <button className="ghost" onClick={() => listRemove("glossary", i)}>Remove</button>
              </div>
            ))}
            <button className="ghost" onClick={() => upd({ glossary: [...(cfg.glossary ?? []), { term: "", say_as: "", meaning: "" }] })}>+ Add term</button>
            <p className="muted small">Changes apply to the next call after you save a new version. Use Simulate to hear the effect before publishing.</p>
          </div>
        );
      })()}

      {tab === "business" && (
        <div className="section form">
          <label><span className="row" style={{ alignItems: "center" }}>Description <AskAi assistantId={cfg.assistant_id} field="description" current={cfg.business.description} website={cfg.business.website} onInsert={(t) => upd({ business: { ...cfg.business, description: t } })} /></span><textarea value={cfg.business.description} onChange={(e) => upd({ business: { ...cfg.business, description: e.target.value } })} /></label>
          <div className="two">
            <label>Website <input value={cfg.business.website ?? ""} onChange={(e) => upd({ business: { ...cfg.business, website: e.target.value || null } })} /></label>
            <label>Phone <input value={cfg.business.phone ?? ""} onChange={(e) => upd({ business: { ...cfg.business, phone: e.target.value || null } })} /></label>
            <label>Email <input value={cfg.business.email ?? ""} onChange={(e) => upd({ business: { ...cfg.business, email: e.target.value || null } })} /></label>
            <label>Address <input value={cfg.business.address ?? ""} onChange={(e) => upd({ business: { ...cfg.business, address: e.target.value || null } })} /></label>
          </div>
          <label><span className="row" style={{ alignItems: "center" }}>Services (one per line) <AskAi assistantId={cfg.assistant_id} field="services" current={cfg.business.services.join("\n")} website={cfg.business.website} placeholder="e.g. List the services a domestic plumbing firm offers, taken from www.parliodemo.co.uk" onInsert={(t) => upd({ business: { ...cfg.business, services: t.split("\n").map((s) => s.trim()).filter(Boolean) } })} /></span>
            <textarea value={cfg.business.services.join("\n")} onChange={(e) => upd({ business: { ...cfg.business, services: e.target.value.split("\n").map((s) => s.trim()).filter(Boolean) } })} />
          </label>
          {(() => {
            const ws: WebsiteSearchConfig = cfg.website_search ?? { enabled: false, extra_urls: [], max_pages: 12 };
            const setWs = (p: Partial<WebsiteSearchConfig>) => upd({ website_search: { ...ws, ...p } });
            return (
              <>
                <h2 style={{ marginTop: "1.2rem" }} id="live-website-search">Live website search</h2>
                <p className="hint">Let the assistant look answers up on <b>your own website only</b> during a call — prices, opening times, policies, products — when they are not in its FAQs. It never browses anywhere else and only quotes what your pages say.</p>
                <label className="small check">
                  <input type="checkbox" checked={ws.enabled} disabled={!cfg.business.website} onChange={(e) => setWs({ enabled: e.target.checked })} /> Search {cfg.business.website ? <code>{cfg.business.website}</code> : "the website above (enter one first)"} during calls
                </label>
                {ws.enabled && (
                  <div className="two">
                    <label>Extra pages to include (one URL per line, same website)
                      <textarea value={ws.extra_urls.join("\n")} placeholder={`${cfg.business.website ?? ""}/prices`} onChange={(e) => setWs({ extra_urls: e.target.value.split("\n") })} onBlur={() => setWs({ extra_urls: ws.extra_urls.map((u) => u.trim()).filter(Boolean) })} />
                    </label>
                    <label>Pages to index (1–40)
                      <input type="number" min={1} max={40} value={ws.max_pages} onChange={(e) => setWs({ max_pages: Math.min(40, Math.max(1, Number(e.target.value) || 1)) })} />
                    </label>
                  </div>
                )}
              </>
            );
          })()}
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
          {(() => {
            const hols: Holiday[] = cfg.hours.holidays ?? [];
            const setHols = (holidays: Holiday[]) => upd({ hours: { ...cfg.hours, holidays } });
            const edit = (i: number, p: Partial<Holiday>) => setHols(hols.map((h, j) => (j === i ? { ...h, ...p } : h)));
            return (
              <>
                <h3 style={{ margin: "0.75rem 0 0.25rem" }}>Holidays &amp; closures</h3>
                <p className="hint">Dated overrides of the weekly hours — closed all day, or open for shorter hours. The assistant mentions upcoming closures when relevant and uses the holiday greeting on the day.</p>
                {hols.length > 0 && (
                  <div className="hours-grid" style={{ gridTemplateColumns: "auto 1fr auto auto auto auto" }}>
                    {hols.map((h, i) => (
                      <div key={i} style={{ display: "contents" }}>
                        <input type="date" value={h.day} onChange={(e) => edit(i, { day: e.target.value })} />
                        <input value={h.name} placeholder="e.g. Christmas Day" onChange={(e) => edit(i, { name: e.target.value })} />
                        <input type="time" disabled={h.closed} value={h.hours?.open.slice(0, 5) ?? ""} onChange={(e) => edit(i, { hours: { open: e.target.value, close: h.hours?.close ?? "13:00" } })} />
                        <input type="time" disabled={h.closed} value={h.hours?.close.slice(0, 5) ?? ""} onChange={(e) => edit(i, { hours: { open: h.hours?.open ?? "09:00", close: e.target.value } })} />
                        <button type="button" className="ghost" onClick={() => edit(i, h.closed ? { closed: false, hours: h.hours ?? { open: "09:00", close: "13:00" } } : { closed: true })}>{h.closed ? "Closed all day" : "Reduced hours"}</button>
                        <button type="button" className="danger" onClick={() => setHols(hols.filter((_, j) => j !== i))}>✕</button>
                      </div>
                    ))}
                  </div>
                )}
                <button type="button" className="ghost" onClick={() => setHols([...hols, { day: new Date().toISOString().slice(0, 10), name: "Holiday", closed: true, hours: null }])}>+ Add holiday</button>
              </>
            );
          })()}
        </div>
      )}

      {tab === "rules" && (
        <div className="section">
          <h2>Key business rules</h2>
          <p className="hint">Short, unambiguous instructions the assistant must always follow, e.g. “Never confirm a booking without a phone number”.</p>
          {cfg.rules.map((r, i) => (
            <div className="list-row" key={r.id ?? i}>
              <input value={r.name} placeholder="Rule name" onChange={(e) => listEdit<BusinessRule>("rules", i, { name: e.target.value })} />
              <div>
                <textarea value={r.instruction} placeholder="Instruction" onChange={(e) => listEdit<BusinessRule>("rules", i, { instruction: e.target.value })} />
                <AskAi assistantId={cfg.assistant_id} field="rule" current={r.instruction} context={r.name || null} website={cfg.business.website} placeholder="e.g. Never book same-day jobs after 3pm; offer next morning instead" onInsert={(t) => listEdit<BusinessRule>("rules", i, { instruction: t })} />
              </div>
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
                  <div>
                    <textarea value={f.answer} placeholder="Answer" onChange={(e) => listEdit<Faq>("faqs", i, { answer: e.target.value })} />
                    <AskAi assistantId={cfg.assistant_id} field="faq_answer" current={f.answer} context={f.question || null} website={cfg.business.website} placeholder="e.g. We cover all of Greater Manchester, call-out fee £60, free quotes" onInsert={(t) => listEdit<Faq>("faqs", i, { answer: t })} />
                  </div>
                  <span>
                    <label className="small"><input type="checkbox" checked={f.enabled} onChange={(e) => listEdit<Faq>("faqs", i, { enabled: e.target.checked })} /> on</label>{" "}
                    {f.source !== "manual" && <span className="pill">{humanize(f.source)}</span>}{" "}
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
              <div>
                <textarea value={s.template} placeholder="Message template" maxLength={320} onChange={(e) => listEdit<SmsScenario>("sms_scenarios", i, { template: e.target.value })} />
                <AskAi assistantId={cfg.assistant_id} field="sms_template" current={s.template} context={`${s.name} (${s.trigger})`} website={cfg.business.website} placeholder="e.g. Thank them for calling and send our booking link" onInsert={(t) => listEdit<SmsScenario>("sms_scenarios", i, { template: t.slice(0, 320) })} />
              </div>
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
          <label style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
            <input type="checkbox" disabled={!cfg.recording.enabled} checked={cfg.recording.record_transfers} onChange={(e) => upd({ recording: { ...cfg.recording, record_transfers: e.target.checked } })} /> Record transferred calls (keep recording the caller and your team member after a transfer)
          </label>
          <p className="hint">Useful for sampling how your team handles calls. Warm transfers only — the team member&apos;s side is saved as its own track, and their talk time appears on the Transfers page. Switch off any time.</p>
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
          <h2 id="call-screening">Call screening</h2>
          <p className="hint">Screened callers are asked who they are and why they are calling before the assistant helps; sales pitches, robocalls and silent lines are ended politely. Known contacts and the numbers you allow below are never screened.</p>
          <label>Screen
            <select value={cfg.screening.mode} onChange={(e) => upd({ screening: { ...cfg.screening, mode: e.target.value as ScreeningMode } })}>
              <option value="off">Nobody (answer every call normally)</option>
              <option value="unknown">Unknown callers only (no contact record)</option>
              <option value="all">Every caller</option>
            </select>
          </label>
          <label className="small check"><input type="checkbox" checked={cfg.screening.block_spam} onChange={(e) => upd({ screening: { ...cfg.screening, block_spam: e.target.checked } })} /> Reject numbers on your spam list and the platform-wide spam list before answering</label>
          <label className="small check"><input type="checkbox" checked={cfg.screening.block_withheld} onChange={(e) => upd({ screening: { ...cfg.screening, block_withheld: e.target.checked } })} /> Reject withheld / anonymous caller IDs (off by default: many genuine customers withhold their number)</label>
          <label>Always allow (never screened or blocked)
            <textarea value={bulkAllowed || cfg.screening.allow_numbers.join("\n")} onChange={(e) => setBulkAllowed(e.target.value)} onBlur={() => { if (bulkAllowed) { upd({ screening: { ...cfg.screening, allow_numbers: Array.from(new Set(bulkAllowed.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean))) } }); setBulkAllowed(""); } }} placeholder="+447700900123" />
          </label>
          <h2 style={{ marginTop: "1.2rem" }}>Blocked numbers</h2>
          <p className="hint">Calls from these numbers are ended immediately without answering and logged as “blocked”. Use E.164 (e.g. +447700900123).</p>
          <textarea value={bulkBlocked || cfg.blocked_numbers.join("\n")} onChange={(e) => setBulkBlocked(e.target.value)} onBlur={() => { if (bulkBlocked) { upd({ blocked_numbers: Array.from(new Set(bulkBlocked.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean))) }); setBulkBlocked(""); } }} />
          <p className="small muted">{cfg.blocked_numbers.length} blocked</p>
        </div>
      )}

      {tab === "afterhours" && (() => {
        const ah: AfterHoursPersona = cfg.after_hours ?? { enabled: false, greeting: "", holiday_greeting: "", tone: "", instructions: "", intake_only: false, transfer: "on_call_only", quote_next_opening: true };
        const setAh = (p: Partial<AfterHoursPersona>) => upd({ after_hours: { ...ah, ...p } });
        return (
        <div className="section form">
          <h2>Closed-hours persona</h2>
          <p className="hint">When your Hours say you are closed (or it is a holiday) the assistant can switch to a different greeting, tone and set of rules. Leave off to behave exactly the same around the clock.</p>
          <label className="small check"><input type="checkbox" checked={ah.enabled} onChange={(e) => setAh({ enabled: e.target.checked })} /> Use a separate persona outside opening hours</label>
          {ah.enabled && (
            <>
              <label><span className="row" style={{ alignItems: "center" }}>After-hours greeting <AskAi assistantId={cfg.assistant_id} field="greeting" current={ah.greeting} website={cfg.business.website} placeholder="e.g. a warm out-of-hours greeting that says we're closed but can still take details" onInsert={(t) => setAh({ greeting: t.replace(/\s+/g, " ").trim() })} /></span><input value={ah.greeting} onChange={(e) => setAh({ greeting: e.target.value })} /></label>
              <label>Holiday greeting (optional — falls back to the after-hours greeting)<input value={ah.holiday_greeting} placeholder="Hi, thanks for calling {business_name}. We're closed for the bank holiday, but I can take your details…" onChange={(e) => setAh({ holiday_greeting: e.target.value })} /></label>
              <div className="two">
                <label>Tone in this window
                  <select value={ah.tone} onChange={(e) => setAh({ tone: e.target.value })}>
                    <option value="">Same as daytime</option>
                    {["calm and reassuring", "brief and efficient", "warm and apologetic", "formal and courteous"].map((t) => <option key={t}>{t}</option>)}
                  </select>
                </label>
                <label>Transfers when closed
                  <select value={ah.transfer} onChange={(e) => setAh({ transfer: e.target.value as AfterHoursPersona["transfer"] })}>
                    <option value="on_call_only">Emergencies only — to on-call destinations</option>
                    <option value="never">Never transfer, always take a message</option>
                    <option value="normal">Same as daytime (destination hours still apply)</option>
                  </select>
                </label>
              </div>
              <label className="small check"><input type="checkbox" checked={ah.intake_only} onChange={(e) => setAh({ intake_only: e.target.checked })} /> Intake only — take name, number and reason for a callback rather than trying to resolve the enquiry (simple FAQs still answered)</label>
              <label className="small check"><input type="checkbox" checked={ah.quote_next_opening} onChange={(e) => setAh({ quote_next_opening: e.target.checked })} /> Tell callers when you reopen (worked out from Hours and holidays)</label>
              <label><span className="row" style={{ alignItems: "center" }}>Extra after-hours rules <AskAi assistantId={cfg.assistant_id} field="persona_extra" current={ah.instructions} website={cfg.business.website} placeholder="e.g. Out of hours we only attend burst pipes and no heating; quote the £120 call-out" onInsert={(t) => setAh({ instructions: t })} /></span><textarea value={ah.instructions} placeholder="e.g. Out-of-hours call-outs are £120 and only for emergencies. Never promise a same-day visit." onChange={(e) => setAh({ instructions: e.target.value })} /></label>
            </>
          )}
          <h2 style={{ marginTop: "1.2rem" }}>No-answer behaviour</h2>
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
        );
      })()}

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

      {blocked && (
        <div className="section" style={{ borderColor: "var(--bad-fg)", marginTop: "1rem" }}>
          <h2>Not published — the regression pack found a problem</h2>
          <p className="hint">
            {blocked.candidate_passed}/{blocked.pack_size} regression tests pass with this change (live version: {blocked.baseline_passed}/{blocked.pack_size}).
            Fix the wording below, or force-publish if you&apos;re sure. Manage the pack under Quality → Regression pack.
          </p>
          <ul className="small" style={{ color: "var(--bad-fg)", margin: "0 0 0.6rem 1rem" }}>
            {blocked.regressions.map((r, i) => <li key={i}>{r}</li>)}
            {blocked.deltas.filter((d) => !d.after_passed && !d.before_passed).map((d) => <li key={d.scenario_id} style={{ color: "var(--muted)" }}>{d.scenario_name}: already failing on the live version ({d.after_failures.join("; ") || "low score"})</li>)}
          </ul>
          <div style={{ display: "flex", gap: "0.5rem" }}>
            <button onClick={() => setBlocked(null)}>Keep editing</button>
            <button className="ghost" disabled={saving} onClick={() => save(true)}>Publish anyway</button>
          </div>
        </div>
      )}
      <SetupNextStep tenant={cfg.tenant_id} step="assistant" refreshKey={versions.length} done="Your assistant is set up. Publish any changes, then carry on with the checklist." />
      {toast && <div className="toast">{toast}</div>}
    </>
  );
}
