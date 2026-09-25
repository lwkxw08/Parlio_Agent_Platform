"use client";

import { useEffect, useState } from "react";
import {
  type BusinessInfo, type Faq, type OnboardingResult, type Schedule, type Vertical, type VerticalPlaybook,
  type VoiceConfig, type WebsiteAnalysis, fetchVerticals, request,
} from "@/lib/api";
import VoicePicker from "@/app/assistant/voice-picker";

type Place = { place_id: string; name: string; address: string | null; phone: string | null; website: string | null; rating: number | null; opening_hours: string[] };
type Analyse = { analysis: WebsiteAnalysis; config_patch: { business_name?: string; business?: BusinessInfo; faqs?: Faq[] } };

const STEPS = ["Find your business", "Confirm details", "Done"];
const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];

export default function Onboarding() {
  const [step, setStep] = useState(0);
  const [vertical, setVertical] = useState<Vertical>("general");
  const [verticals, setVerticals] = useState<VerticalPlaybook[]>([]);
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
  const [voice, setVoice] = useState<VoiceConfig | null>(null);
  const [result, setResult] = useState<OnboardingResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { fetchVerticals().then((v) => v && setVerticals(v)); }, []);

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
    setStep(1);
  };

  const finish = async () => {
    setBusy(true); setError(null);
    const r = await request<OnboardingResult>("/v1/onboarding", {
      method: "POST",
      body: JSON.stringify({
        organisation_name: orgName, assistant_name: assistantName, business, hours, faqs, languages: ["en"], greeting: greeting || null, voice, vertical,
      }),
    });
    setBusy(false);
    if (!r.ok) return setError(r.error);
    setResult(r.data);
    setStep(2);
  };

  const selectedPlaybook = verticals.find((v) => v.id === vertical);
  const trialEnds = result?.trial_ends_at ? new Date(result.trial_ends_at).toLocaleDateString("en-GB") : null;
  const defaultGreeting = (selectedPlaybook?.greeting ?? "Hi, thanks for calling {business_name}. How can I help you today?").replace("{business_name}", orgName || "{business_name}");

  return (
    <>
      <h1>Set up your AI assistant</h1>
      <div className="steps">
        {STEPS.map((s, i) => <span key={s} className={i === step ? "active" : i < step ? "done" : ""}>{i + 1}. {s}</span>)}
      </div>

      {step === 0 && (
        <div className="section form">
          <h2>Where can we learn about your business?</h2>
          <p className="hint">We read your website to pre-fill your description, contact details, opening hours and FAQs — about a minute, and you can edit everything afterwards. Your free trial starts with every feature unlocked; no card needed.</p>
          <label>Website
            <div style={{ display: "flex", gap: 8 }}>
              <input style={{ flex: 1 }} placeholder="https://www.example.co.uk" value={url} onChange={(e) => setUrl(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); void analyse(); } }} />
              <button type="button" className="ghost" disabled={busy || !url} onClick={analyse}>{busy ? "Reading…" : "Analyse"}</button>
            </div>
          </label>
          <label>Or search Google Business Profile
            <div style={{ display: "flex", gap: 8 }}>
              <input style={{ flex: 1 }} placeholder="Business name and town" value={query} onChange={(e) => setQuery(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); void searchPlaces(); } }} />
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
          <label>Type of business
            <select value={vertical} onChange={(e) => setVertical(e.target.value as Vertical)}>
              {verticals.length ? verticals.map((v) => <option key={v.id} value={v.id}>{v.name}</option>) : <option value="general">General business</option>}
            </select>
            {selectedPlaybook && <span className="small muted">{selectedPlaybook.tagline} — we&apos;ll pre-load a starter greeting, FAQs and rules.</span>}
          </label>
          {error && <p className="small" style={{ color: "var(--bad-fg)" }}>{error}</p>}
          <div style={{ display: "flex", gap: 8 }}>
            <button className="primary" onClick={() => setStep(1)}>{analysis ? "Looks right — continue" : "Skip and enter manually"}</button>
          </div>
        </div>
      )}

      {step === 1 && (
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
          <h2 style={{ marginTop: "0.5rem" }}>Your assistant</h2>
          <div className="two">
            <label>Assistant name <input value={assistantName} onChange={(e) => setAssistantName(e.target.value)} /></label>
            <label>Greeting
              <input placeholder={defaultGreeting} value={greeting} onChange={(e) => setGreeting(e.target.value)} />
              <span className="small muted">Leave blank to use the greeting shown.</span>
            </label>
          </div>
          <h3 style={{ margin: "0.5rem 0 0.25rem" }}>Voice</h3>
          <p className="small muted" style={{ marginTop: 0 }}>Listen and pick the voice {assistantName || "your assistant"} will answer with. You can change it any time in Assistant Studio → Voice.</p>
          <VoicePicker value={voice ?? { provider: "cartesia", voice_id: "", speed: null }} businessName={orgName} onChange={setVoice} />
          <p className="hint">
            {faqs.length ? `${faqs.length} FAQs from your website${selectedPlaybook ? ` and ${selectedPlaybook.faqs.length} starter FAQs` : ""} will be loaded.` : selectedPlaybook ? `${selectedPlaybook.faqs.length} starter FAQs will be loaded.` : ""}
            {" "}FAQs, business rules and more can be fine-tuned in Assistant Studio after setup.
          </p>
          {error && <p className="small" style={{ color: "var(--bad-fg)" }}>{error}</p>}
          <div style={{ display: "flex", gap: 8 }}>
            <button className="ghost" onClick={() => setStep(0)}>Back</button>
            <button className="primary" disabled={!orgName || busy} onClick={finish}>{busy ? "Creating…" : "Create my assistant"}</button>
          </div>
        </div>
      )}

      {step === 2 && result && (
        <div className="section">
          <h2>{result.assistant.name} is ready for {result.assistant.business_name}</h2>
          <p className="hint">
            Your free trial runs until {trialEnds ?? "the trial ends"} with every feature unlocked — no card needed. Next: make a test call, get your phone number and divert your line. You can pick a plan under Billing whenever you&apos;re ready, and change the voice or greeting in Assistant Studio → Voice.
          </p>
          {error && <p className="small" style={{ color: "var(--bad-fg)" }}>{error}</p>}
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <a className="btn primary" href={`/setup?tenant=${result.tenant_id}`}>Finish setup &amp; make a test call</a>
            <a className="btn" href={`/assistant?id=${result.assistant.assistant_id}`}>Open Assistant Studio</a>
            <a className="btn" href="/team">Invite your team</a>
          </div>
        </div>
      )}
    </>
  );
}
