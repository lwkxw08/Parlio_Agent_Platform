"use client";

import { useState } from "react";
import { type Assistant, type BusinessInfo, type Faq, type Schedule, type WebsiteAnalysis, request } from "@/lib/api";

type Place = { place_id: string; name: string; address: string | null; phone: string | null; website: string | null; rating: number | null; opening_hours: string[] };
type Analyse = { analysis: WebsiteAnalysis; config_patch: { business_name?: string; business?: BusinessInfo; faqs?: Faq[] } };

const STEPS = ["Find your business", "Confirm details", "FAQs", "Assistant", "Done"];
const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];

export default function Onboarding() {
  const [step, setStep] = useState(0);
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
  const [assistantName, setAssistantName] = useState("Parlio");
  const [greeting, setGreeting] = useState("");
  const [languages, setLanguages] = useState<string[]>(["en"]);
  const [result, setResult] = useState<Assistant | null>(null);
  const [error, setError] = useState<string | null>(null);

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
    const r = await request<{ tenant_id: string; assistant: Assistant }>("/v1/onboarding", {
      method: "POST",
      body: JSON.stringify({ organisation_name: orgName, assistant_name: assistantName, business, hours, faqs, languages, greeting: greeting || null }),
    });
    setBusy(false);
    if (!r.ok) return setError(r.error);
    setResult(r.data.assistant);
    setStep(4);
  };

  return (
    <>
      <h1>Set up your AI receptionist</h1>
      <div className="steps">
        {STEPS.map((s, i) => <span key={s} className={i === step ? "active" : i < step ? "done" : ""}>{i + 1}. {s}</span>)}
      </div>

      {step === 0 && (
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
          {error && <p className="small" style={{ color: "#ff7b86" }}>{error}</p>}
          <div><button className="primary" onClick={() => setStep(1)}>{analysis ? "Looks right — continue" : "Skip and enter manually"}</button></div>
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
          <div style={{ display: "flex", gap: 8 }}>
            <button className="ghost" onClick={() => setStep(0)}>Back</button>
            <button className="primary" disabled={!orgName} onClick={() => setStep(2)}>Continue</button>
          </div>
        </div>
      )}

      {step === 2 && (
        <div className="section">
          <h2>Frequently asked questions</h2>
          <p className="hint">These are answered instantly on calls. We found {analysis?.faqs.length ?? 0} on your website; add the questions callers ask most.</p>
          {faqs.map((f, i) => (
            <div className="list-row" key={i}>
              <input value={f.question} placeholder="Question" onChange={(e) => { const a = [...faqs]; a[i] = { ...f, question: e.target.value }; setFaqs(a); }} />
              <textarea value={f.answer} placeholder="Answer" onChange={(e) => { const a = [...faqs]; a[i] = { ...f, answer: e.target.value }; setFaqs(a); }} />
              <button className="danger" onClick={() => setFaqs(faqs.filter((_, j) => j !== i))}>✕</button>
            </div>
          ))}
          <div style={{ display: "flex", gap: 8, marginTop: "1rem" }}>
            <button className="ghost" onClick={() => setFaqs([...faqs, { category: "general", question: "", answer: "", enabled: true, source: "manual" }])}>+ Add FAQ</button>
            <button className="ghost" onClick={() => setStep(1)}>Back</button>
            <button className="primary" onClick={() => setStep(3)}>Continue</button>
          </div>
        </div>
      )}

      {step === 3 && (
        <div className="section form">
          <h2>Your assistant</h2>
          <div className="two">
            <label>Assistant name <input value={assistantName} onChange={(e) => setAssistantName(e.target.value)} /></label>
            <label>Languages
              <select multiple value={languages} onChange={(e) => setLanguages(Array.from(e.target.selectedOptions).map((o) => o.value))} style={{ minHeight: "5rem" }}>
                {[["en", "English"], ["cy", "Welsh"], ["pl", "Polish"], ["ur", "Urdu"], ["fr", "French"], ["es", "Spanish"], ["de", "German"]].map(([c, l]) => <option key={c} value={c}>{l}</option>)}
              </select>
            </label>
          </div>
          <label>Greeting
            <input placeholder={`Hi, thanks for calling ${orgName || "{business_name}"}. How can I help you today?`} value={greeting} onChange={(e) => setGreeting(e.target.value)} />
          </label>
          <p className="hint">Voice, tone, business rules, SMS scenarios, blocked numbers and more are in Assistant Studio after setup.</p>
          {error && <p className="small" style={{ color: "#ff7b86" }}>{error}</p>}
          <div style={{ display: "flex", gap: 8 }}>
            <button className="ghost" onClick={() => setStep(2)}>Back</button>
            <button className="primary" disabled={busy} onClick={finish}>{busy ? "Creating…" : "Create my assistant"}</button>
          </div>
        </div>
      )}

      {step === 4 && result && (
        <div className="section">
          <h2>{result.name} is ready for {result.business_name}</h2>
          <p className="hint">Organisation <code>{result.tenant_id}</code> created with you as owner. Next: connect a phone number and forward your calls.</p>
          <div style={{ display: "flex", gap: 8 }}>
            <a className="btn" href={`/assistant?id=${result.assistant_id}`}>Open Assistant Studio</a>
            <a className="btn" href="/launch">How to launch</a>
            <a className="btn" href="/team">Invite your team</a>
          </div>
        </div>
      )}
    </>
  );
}
