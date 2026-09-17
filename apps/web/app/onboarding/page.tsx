"use client";

import { useEffect, useState } from "react";
import {
  type BusinessInfo, type CallVolume, type CheckoutSession, type Faq, type JourneyChannel, type JourneyTask,
  type OnboardingResult, type PlanRecommendation, type Questionnaire, type Schedule, type Vertical, type VerticalPlaybook,
  type WebsiteAnalysis, fetchVerticals, gbp, recommendPlan, request,
} from "@/lib/api";

type Place = { place_id: string; name: string; address: string | null; phone: string | null; website: string | null; rating: number | null; opening_hours: string[] };
type Analyse = { analysis: WebsiteAnalysis; config_patch: { business_name?: string; business?: BusinessInfo; faqs?: Faq[] } };

const STEPS = ["Call volume", "What it should do", "Find your business", "Confirm details", "Your plan", "FAQs", "Assistant", "Done"];
const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];

const VOLUMES: [CallVolume, string, string][] = [
  ["0-50", "Up to 50 calls a month", "A few calls a day — sole traders and small teams"],
  ["50-200", "50 – 200 calls a month", "Busy small business with a steady phone"],
  ["200-500", "200 – 500 calls a month", "Multiple staff, several enquiries an hour"],
  ["500+", "500+ calls a month", "High volume, multiple sites or campaigns"],
];
const TASKS: [JourneyTask, string, string][] = [
  ["faqs", "Answer common questions", "Opening hours, prices, directions, services"],
  ["messages", "Take messages", "Name, number, reason — sent to your team instantly"],
  ["book", "Book appointments", "Live availability from your calendar or booking link"],
  ["transfer", "Transfer to staff", "Warm transfers to people or departments"],
  ["info", "Give business information", "Describe what you do and how to buy"],
  ["qualify", "Qualify leads", "Ask the right questions and score enquiries"],
  ["payments", "Take payments", "Send secure payment links during the call"],
  ["outbound", "Call people back", "Missed-call text-back, callbacks, reminders"],
  ["webchat", "Web chat & click-to-talk", "Same assistant on your website"],
];
const CHANNELS: [JourneyChannel, string][] = [["phone", "Phone"], ["sms", "SMS"], ["whatsapp", "WhatsApp"], ["webchat", "Web chat"]];
const LANGS: [string, string][] = [["en", "English"], ["cy", "Welsh"], ["pl", "Polish"], ["ur", "Urdu"], ["fr", "French"], ["es", "Spanish"], ["de", "German"]];

const toggle = <T,>(arr: T[], v: T) => (arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v]);

export default function Onboarding() {
  const [step, setStep] = useState(0);
  const [volume, setVolume] = useState<CallVolume>("50-200");
  const [tasks, setTasks] = useState<JourneyTask[]>(["faqs", "messages"]);
  const [channels, setChannels] = useState<JourneyChannel[]>(["phone"]);
  const [teamSize, setTeamSize] = useState(3);
  const [locations, setLocations] = useState(1);
  const [sovereign, setSovereign] = useState(false);
  const [vertical, setVertical] = useState<Vertical>("general");
  const [verticals, setVerticals] = useState<VerticalPlaybook[]>([]);
  const [languages, setLanguages] = useState<string[]>(["en"]);
  const [url, setUrl] = useState("");
  const [query, setQuery] = useState("");
  const [places, setPlaces] = useState<Place[] | null>(null);
  const [placesError, setPlacesError] = useState<string | null>(null);
  const [analysis, setAnalysis] = useState<WebsiteAnalysis | null>(null);
  const [busy, setBusy] = useState(false);
  const [orgName, setOrgName] = useState("");
  const [business, setBusiness] = useState<BusinessInfo>({ description: "", website: null, address: null, phone: null, email: null, services: [] });
  const [hours, setHours] = useState<Schedule>({ timezone: "Europe/London", always: false, hours: Object.fromEntries(DAYS.slice(0, 5).map((d) => [d, { open: "09:00", close: "17:30" }])) });
  const [faqs, setFaqs] = useState<Faq[]>([]);
  const [assistantName, setAssistantName] = useState("ParlioTec");
  const [greeting, setGreeting] = useState("");
  const [rec, setRec] = useState<PlanRecommendation | null>(null);
  const [planId, setPlanId] = useState<string | null>(null);
  const [result, setResult] = useState<OnboardingResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { fetchVerticals().then((v) => v && setVerticals(v)); }, []);

  const questionnaire = (): Questionnaire => ({
    monthly_calls: volume, tasks, team_size: teamSize, channels, languages, integrations: [], vertical, sovereign_uk: sovereign, locations,
  });

  const loadRecommendation = async () => {
    setBusy(true); setError(null);
    const r = await recommendPlan(questionnaire());
    setBusy(false);
    if (!r.ok) return setError(r.error);
    setRec(r.data);
    setPlanId(r.data.plan_id);
    setStep(4);
  };

  const analyse = async () => {
    if (!url) return;
    setBusy(true); setError(null);
    const r = await request<Analyse>("/v1/onboarding/analyse-website", { method: "POST", body: JSON.stringify({ url }) });
    setBusy(false);
    if (!r.ok) return setError(r.error);
    const a = r.data.analysis;
    setAnalysis(a);
    if (!a.reachable) setError(`Couldn't read that site (${a.error ?? "unreachable"}) — fill the details in manually.`);
    if (a.business_name && !orgName) setOrgName(a.business_name);
    setBusiness({ ...a.business, website: a.business.website ?? url });
    setFaqs(a.faqs);
  };

  const searchPlaces = async () => {
    if (!query) return;
    setBusy(true); setPlacesError(null);
    const r = await request<Place[]>(`/v1/onboarding/places?query=${encodeURIComponent(query)}`);
    setBusy(false);
    if (!r.ok) { setPlaces(null); return setPlacesError(r.status === 501 ? "Google Places lookup isn't configured yet — use your website instead." : r.error); }
    setPlaces(r.data);
  };

  const pickPlace = (p: Place) => {
    setOrgName(p.name);
    setBusiness((b) => ({ ...b, address: p.address ?? b.address, phone: p.phone ?? b.phone, website: p.website ?? b.website }));
    if (p.website && !url) setUrl(p.website);
    setStep(3);
  };

  const finish = async () => {
    setBusy(true); setError(null);
    const r = await request<OnboardingResult>("/v1/onboarding", {
      method: "POST",
      body: JSON.stringify({
        organisation_name: orgName, assistant_name: assistantName, business, hours, faqs, languages, greeting: greeting || null,
        vertical, questionnaire: questionnaire(), plan_id: planId,
      }),
    });
    setBusy(false);
    if (!r.ok) return setError(r.error);
    setResult(r.data);
    setStep(7);
  };

  const addPayment = async () => {
    if (!result?.plan_id) return;
    setBusy(true); setError(null);
    const r = await request<CheckoutSession>(`/v1/billing/checkout?tenant_id=${result.tenant_id}`, {
      method: "POST", body: JSON.stringify({ plan_id: result.plan_id, return_url: `${window.location.origin}/setup?tenant=${result.tenant_id}` }),
    });
    setBusy(false);
    if (!r.ok) return setError(r.error);
    if (r.data.provider !== "simulated") { window.location.href = r.data.url; return; }
    window.location.href = `/setup?tenant=${result.tenant_id}&checkout=simulated`;
  };

  const selectedPlaybook = verticals.find((v) => v.id === vertical);
  const chosen = rec?.options.find((o) => o.plan_id === planId);
  const trialEnds = result?.trial_ends_at ? new Date(result.trial_ends_at).toLocaleDateString("en-GB") : null;

  return (
    <>
      <h1>Set up your AI receptionist</h1>
      <div className="steps">
        {STEPS.map((s, i) => <span key={s} className={i === step ? "active" : i < step ? "done" : ""}>{i + 1}. {s}</span>)}
      </div>

      {step === 0 && (
        <div className="section form">
          <h2>Roughly how many calls does your business get each month?</h2>
          <p className="hint">This helps us recommend the right plan. You can change plan at any time — nothing is charged during the free trial.</p>
          <div className="grid">
            {VOLUMES.map(([v, title, sub]) => (
              <button key={v} type="button" className={`card choice ${volume === v ? "selected" : ""}`} onClick={() => setVolume(v)}>
                <div className="value" style={{ fontSize: "1.05rem" }}>{title}</div>
                <div className="small muted">{sub}</div>
              </button>
            ))}
          </div>
          <div><button className="primary" onClick={() => setStep(1)}>Continue</button></div>
        </div>
      )}

      {step === 1 && (
        <div className="section form">
          <h2>What should your assistant do?</h2>
          <p className="hint">Pick everything that applies — this shapes the plan we suggest and the starter setup for your assistant.</p>
          <div className="grid">
            {TASKS.map(([t, title, sub]) => (
              <button key={t} type="button" className={`card choice ${tasks.includes(t) ? "selected" : ""}`} onClick={() => setTasks(toggle(tasks, t))}>
                <div className="value" style={{ fontSize: "1rem" }}>{tasks.includes(t) ? "✓ " : ""}{title}</div>
                <div className="small muted">{sub}</div>
              </button>
            ))}
          </div>
          <div className="two">
            <label>Type of business
              <select value={vertical} onChange={(e) => setVertical(e.target.value as Vertical)}>
                {verticals.length ? verticals.map((v) => <option key={v.id} value={v.id}>{v.name}</option>) : <option value="general">General business</option>}
              </select>
              {selectedPlaybook && <span className="small muted">{selectedPlaybook.tagline} — we&apos;ll pre-load a starter greeting, FAQs and rules.</span>}
            </label>
            <label>Languages callers use
              <select multiple value={languages} onChange={(e) => setLanguages(Array.from(e.target.selectedOptions).map((o) => o.value))} style={{ minHeight: "5rem" }}>
                {LANGS.map(([c, l]) => <option key={c} value={c}>{l}</option>)}
              </select>
            </label>
            <label>People who take calls <input type="number" min={1} max={10000} value={teamSize} onChange={(e) => setTeamSize(Math.max(1, Number(e.target.value) || 1))} /></label>
            <label>Locations / branches <input type="number" min={1} max={1000} value={locations} onChange={(e) => setLocations(Math.max(1, Number(e.target.value) || 1))} /></label>
          </div>
          <label>Channels
            <div className="chips">
              {CHANNELS.map(([c, l]) => <button key={c} type="button" className={channels.includes(c) ? "active" : ""} onClick={() => c !== "phone" && setChannels(toggle(channels, c))}>{l}</button>)}
            </div>
          </label>
          <label style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
            <input type="checkbox" checked={sovereign} onChange={(e) => setSovereign(e.target.checked)} /> We need all call data and AI processing to stay in the UK (regulated / public sector)
          </label>
          <div style={{ display: "flex", gap: 8 }}>
            <button className="ghost" onClick={() => setStep(0)}>Back</button>
            <button className="primary" onClick={() => setStep(2)}>Continue</button>
          </div>
        </div>
      )}

      {step === 2 && (
        <div className="section form">
          <h2>Where can we learn about your business?</h2>
          <p className="hint">We read your website to pre-fill your description, contact details, opening hours and FAQs. You can edit everything afterwards.</p>
          <label>Website
            <div style={{ display: "flex", gap: 8 }}>
              <input style={{ flex: 1 }} placeholder="https://www.example.co.uk" value={url} onChange={(e) => setUrl(e.target.value)} />
              <button type="button" className="ghost" disabled={busy || !url} onClick={analyse}>{busy ? "Reading…" : "Analyse"}</button>
            </div>
          </label>
          <label>Or search Google Business Profile
            <div style={{ display: "flex", gap: 8 }}>
              <input style={{ flex: 1 }} placeholder="Business name and town" value={query} onChange={(e) => setQuery(e.target.value)} />
              <button type="button" className="ghost" disabled={busy || !query} onClick={searchPlaces}>Search</button>
            </div>
          </label>
          {placesError && <p className="small muted">{placesError}</p>}
          {places && (
            <ul className="small">
              {places.map((p) => <li key={p.place_id}><a href="#" onClick={(e) => { e.preventDefault(); pickPlace(p); }}>{p.name}</a> — {p.address ?? ""} {p.rating ? `· ★ ${p.rating}` : ""}</li>)}
              {!places.length && <li className="muted">No matches</li>}
            </ul>
          )}
          {analysis && (
            <div className="card">
              <h2>{analysis.business_name ?? "Website read"}</h2>
              <p className="small muted">{analysis.business.description || "No description found"}</p>
              <p className="small">{analysis.faqs.length} FAQs · {analysis.business.services.length} services · {analysis.opening_hours_text.length ? "opening hours found" : "no opening hours found"}</p>
            </div>
          )}
          {error && <p className="small" style={{ color: "var(--bad-fg)" }}>{error}</p>}
          <div style={{ display: "flex", gap: 8 }}>
            <button className="ghost" onClick={() => setStep(1)}>Back</button>
            <button className="primary" onClick={() => setStep(3)}>{analysis ? "Looks right — continue" : "Skip and enter manually"}</button>
          </div>
        </div>
      )}

      {step === 3 && (
        <div className="section form">
          <h2>Confirm your business details</h2>
          <label>Business name <input required value={orgName} onChange={(e) => setOrgName(e.target.value)} /></label>
          <label>What do you do? (the assistant uses this to answer callers)
            <textarea value={business.description} onChange={(e) => setBusiness({ ...business, description: e.target.value })} />
          </label>
          <div className="two">
            <label>Phone <input value={business.phone ?? ""} onChange={(e) => setBusiness({ ...business, phone: e.target.value || null })} /></label>
            <label>Email <input value={business.email ?? ""} onChange={(e) => setBusiness({ ...business, email: e.target.value || null })} /></label>
            <label>Address <input value={business.address ?? ""} onChange={(e) => setBusiness({ ...business, address: e.target.value || null })} /></label>
            <label>Website <input value={business.website ?? ""} onChange={(e) => setBusiness({ ...business, website: e.target.value || null })} /></label>
          </div>
          <label>Services (one per line)
            <textarea value={business.services.join("\n")} onChange={(e) => setBusiness({ ...business, services: e.target.value.split("\n").map((s) => s.trim()).filter(Boolean) })} />
          </label>
          <h2 style={{ marginTop: "0.5rem" }}>Opening hours</h2>
          {analysis?.opening_hours_text.length ? <p className="hint">Found on your website: {analysis.opening_hours_text.join(" · ")}</p> : null}
          <label style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
            <input type="checkbox" checked={hours.always} onChange={(e) => setHours({ ...hours, always: e.target.checked })} /> Open 24/7
          </label>
          {!hours.always && (
            <div className="hours-grid">
              {DAYS.map((d) => {
                const h = hours.hours[d];
                const set = (v: { open: string; close: string } | null) => { const hh = { ...hours.hours }; if (v) hh[d] = v; else delete hh[d]; setHours({ ...hours, hours: hh }); };
                return (
                  <div key={d} style={{ display: "contents" }}>
                    <span style={{ textTransform: "capitalize" }}>{d}</span>
                    <input type="time" disabled={!h} value={h?.open ?? ""} onChange={(e) => h && set({ ...h, open: e.target.value })} />
                    <input type="time" disabled={!h} value={h?.close ?? ""} onChange={(e) => h && set({ ...h, close: e.target.value })} />
                    <button type="button" className="ghost" onClick={() => set(h ? null : { open: "09:00", close: "17:30" })}>{h ? "Closed" : "Open"}</button>
                  </div>
                );
              })}
            </div>
          )}
          {error && <p className="small" style={{ color: "var(--bad-fg)" }}>{error}</p>}
          <div style={{ display: "flex", gap: 8 }}>
            <button className="ghost" onClick={() => setStep(2)}>Back</button>
            <button className="primary" disabled={!orgName || busy} onClick={loadRecommendation}>{busy ? "Working out your plan…" : "See my recommended plan"}</button>
          </div>
        </div>
      )}

      {step === 4 && rec && (
        <div className="section form">
          <h2>Our recommendation for {orgName}</h2>
          <p className="hint">Based on ~{rec.estimated_minutes} call minutes a month and what you asked the assistant to do. Every plan starts with a {rec.trial_days}-day free trial with all features unlocked — no card needed to begin.</p>
          <ul className="small">{rec.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
          <div className="grid">
            {rec.options.map((o) => {
              const enterprise = o.plan_id === "enterprise";
              const selected = planId === o.plan_id;
              return (
                <div key={o.plan_id} className={`card choice ${selected ? "selected" : ""}`} style={{ opacity: o.fits || enterprise ? 1 : 0.7 }}>
                  <div className="row" style={{ justifyContent: "space-between" }}>
                    <strong>{o.name}</strong>
                    {o.plan_id === rec.plan_id && <span className="pill ok">Recommended</span>}
                  </div>
                  <div className="value" style={{ fontSize: "1.3rem" }}>{enterprise ? "Custom" : `${gbp(o.monthly_pence)}/mo`}</div>
                  <div className="small muted">{enterprise ? "Volume pricing, UK-sovereign options, SLA" : `${o.included_minutes} minutes included`}</div>
                  {!enterprise && o.estimated_monthly_pence !== o.monthly_pence && <div className="small">Est. {gbp(o.estimated_monthly_pence)}/mo at your volume (incl. overage)</div>}
                  {!o.fits && o.missing.length > 0 && <div className="small" style={{ color: "var(--bad-fg)" }}>Missing: {o.missing.join(", ")}</div>}
                  <div style={{ marginTop: 8 }}>
                    {enterprise
                      ? <a className="btn" href="mailto:sales@parlio.co.uk?subject=Enterprise%20enquiry">Talk to sales</a>
                      : <button type="button" className={selected ? "primary" : "ghost"} onClick={() => setPlanId(o.plan_id)}>{selected ? "Selected" : "Choose"}</button>}
                  </div>
                </div>
              );
            })}
          </div>
          {chosen && !chosen.fits && <p className="small" style={{ color: "var(--bad-fg)" }}>Heads-up: {chosen.name} doesn&apos;t include everything you asked for ({chosen.missing.join(", ")}). Those features will lock when the trial ends unless you upgrade.</p>}
          {error && <p className="small" style={{ color: "var(--bad-fg)" }}>{error}</p>}
          <div style={{ display: "flex", gap: 8 }}>
            <button className="ghost" onClick={() => setStep(3)}>Back</button>
            <button className="primary" disabled={!planId} onClick={() => setStep(5)}>Continue with {chosen?.name ?? "this plan"}</button>
          </div>
        </div>
      )}

      {step === 5 && (
        <div className="section">
          <h2>Frequently asked questions</h2>
          <p className="hint">These are answered instantly on calls. We found {analysis?.faqs.length ?? 0} on your website{selectedPlaybook ? ` and will add ${selectedPlaybook.faqs.length} starter FAQs for ${selectedPlaybook.name.toLowerCase()}` : ""}; add the questions callers ask most.</p>
          {faqs.map((f, i) => (
            <div className="list-row" key={i}>
              <input value={f.question} placeholder="Question" onChange={(e) => { const a = [...faqs]; a[i] = { ...f, question: e.target.value }; setFaqs(a); }} />
              <textarea value={f.answer} placeholder="Answer" onChange={(e) => { const a = [...faqs]; a[i] = { ...f, answer: e.target.value }; setFaqs(a); }} />
              <button className="danger" onClick={() => setFaqs(faqs.filter((_, j) => j !== i))}>✕</button>
            </div>
          ))}
          <div style={{ display: "flex", gap: 8, marginTop: "1rem" }}>
            <button className="ghost" onClick={() => setFaqs([...faqs, { category: "general", question: "", answer: "", enabled: true, source: "manual" }])}>+ Add FAQ</button>
            <button className="ghost" onClick={() => setStep(4)}>Back</button>
            <button className="primary" onClick={() => setStep(6)}>Continue</button>
          </div>
        </div>
      )}

      {step === 6 && (
        <div className="section form">
          <h2>Your assistant</h2>
          <label>Assistant name <input value={assistantName} onChange={(e) => setAssistantName(e.target.value)} /></label>
          <label>Greeting
            <input placeholder={(selectedPlaybook?.greeting ?? "Hi, thanks for calling {business_name}. How can I help you today?").replace("{business_name}", orgName || "{business_name}")} value={greeting} onChange={(e) => setGreeting(e.target.value)} />
            <span className="small muted">Leave blank to use the {selectedPlaybook ? selectedPlaybook.name.toLowerCase() : "default"} greeting shown.</span>
          </label>
          <p className="hint">Voice, tone, business rules, SMS scenarios, blocked numbers and more are in Assistant Studio after setup.</p>
          {error && <p className="small" style={{ color: "var(--bad-fg)" }}>{error}</p>}
          <div style={{ display: "flex", gap: 8 }}>
            <button className="ghost" onClick={() => setStep(5)}>Back</button>
            <button className="primary" disabled={busy} onClick={finish}>{busy ? "Creating…" : "Create my assistant"}</button>
          </div>
        </div>
      )}

      {step === 7 && result && (
        <div className="section">
          <h2>{result.assistant.name} is ready for {result.assistant.business_name}</h2>
          <p className="hint">
            Organisation <code>{result.tenant_id}</code> created with you as owner.
            {result.plan_id ? ` Your free trial of the ${chosen?.name ?? result.plan_id} plan runs until ${trialEnds ?? "the trial ends"} with every feature unlocked.` : " You're on the free trial."}
          </p>
          {error && <p className="small" style={{ color: "var(--bad-fg)" }}>{error}</p>}
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <a className="btn primary" href={`/setup?tenant=${result.tenant_id}`}>Finish setup &amp; make a test call</a>
            {result.plan_id && <button className="ghost" disabled={busy} onClick={addPayment}>{busy ? "Opening checkout…" : "Add payment details now"}</button>}
            <a className="btn" href={`/assistant?id=${result.assistant.assistant_id}`}>Open Assistant Studio</a>
            <a className="btn" href="/team">Invite your team</a>
          </div>
        </div>
      )}
    </>
  );
}
