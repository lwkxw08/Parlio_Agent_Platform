"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { cap, fetchSiteInfo, gbp, type PublicPlan, signupUrl } from "@/lib/api";

const HIGHLIGHTS: { key: string; label: string }[] = [
  { key: "calendar_booking", label: "Calendar booking (Google / Outlook) with booking rules" },
  { key: "team_scheduling", label: "Team scheduling: pooled team members + Schedule board" },
  { key: "scheduling_tool", label: "Book into ServiceM8 / your scheduling tool" },
  { key: "warm_transfers", label: "Warm (announced) transfers" },
  { key: "whatsapp", label: "WhatsApp channel" },
  { key: "ask_ai", label: "Ask AI analytics" },
  { key: "advisor", label: "AI business advisor" },
  { key: "transcript_search", label: "Transcript & recording search" },
  { key: "outbound", label: "Compliant AI call-backs (outbound)" },
  { key: "byo_sip", label: "BYO SIP trunk / PBX" },
  { key: "white_label", label: "White-label & agency accounts" },
  { key: "sso", label: "SSO / SCIM" },
];

/* API feature strings that duplicate the cap line or an entitlement row above. */
const DUPLICATE = /assistant|engineer|team member|location|dashboard user|calendar booking|team scheduling|scheduling tool|servicem8|warm transfer|whatsapp|ask ai|advisor|search|\bsms\b|number/i;

function PlanCard({ p, featured, trialDays }: { p: PublicPlan; featured: boolean; trialDays: number }) {
  const ents = new Set(p.entitlements);
  return (
    <article className={`plan ${featured ? "featured" : ""}`}>
      {featured && <span className="badge">Most popular</span>}
      <div>
        <h3>{p.name}</h3>
        <p className="muted small">
          {p.id === "starter" && "For one person or a small office that can't afford to miss a call."}
          {p.id === "growth" && "For teams that book appointments and need a real diary."}
          {p.id === "scale" && "For multi-van, multi-site operations and their scheduling tools."}
          {p.enterprise && "Dedicated capacity, UK-sovereign deployment, custom terms."}
        </p>
      </div>
      <div className="price">
        {p.enterprise ? "Let's talk" : gbp(p.monthly_pence)}
        {!p.enterprise && <small> / month + VAT</small>}
      </div>
      <ul>
        <li>{p.enterprise ? "Custom minute bundle" : `${p.included_minutes.toLocaleString("en-GB")} AI minutes included`}{!p.enterprise && <span className="muted"> · then {p.overage_pence_per_minute}p/min</span>}</li>
        <li>{cap(p.included_sms, "SMS")} · {cap(p.included_numbers, "UK number" + (p.included_numbers === 1 ? "" : "s"))}</li>
        <li>{cap(p.max_assistants, "assistant" + (p.max_assistants === 1 ? "" : "s"))} · {cap(p.max_concurrent_calls, "simultaneous calls")}</li>
        <li>{cap(p.max_resources, "bookable team member" + (p.max_resources === 1 ? "" : "s"))} · {cap(p.max_sites, "location" + (p.max_sites === 1 ? "" : "s"))} · {cap(p.max_members, "dashboard user" + (p.max_members === 1 ? "" : "s"))}</li>
        {p.features.filter((f) => !DUPLICATE.test(f)).map((f) => <li key={f}>{f}</li>)}
        {HIGHLIGHTS.map((h) => (
          <li key={h.key} className={ents.has(h.key) ? "" : "off"}>{h.label}</li>
        ))}
      </ul>
      {p.enterprise ? (
        <Link className="btn secondary" href="/contact/?interest=enterprise">Talk to sales</Link>
      ) : (
        <a className={`btn ${featured ? "primary" : "secondary"}`} href={`${signupUrl}?plan=${p.id}`}>
          Start {trialDays}-day free trial
        </a>
      )}
    </article>
  );
}

const FALLBACK_NOTE = "Live pricing couldn't be loaded right now. Please refresh, or contact us for current plans.";

export function Pricing() {
  const [plans, setPlans] = useState<PublicPlan[] | null | undefined>(undefined);
  const [trial, setTrial] = useState(14);
  useEffect(() => {
    let alive = true;
    void fetchSiteInfo().then((s) => {
      if (!alive) return;
      if (!s) { setPlans(null); return; }
      setPlans(s.plans);
      setTrial(s.trial_days);
    });
    return () => { alive = false; };
  }, []);

  if (plans === undefined) return <p className="muted" style={{ textAlign: "center" }}>Loading plans…</p>;
  if (plans === null) {
    return (
      <div className="error-box" style={{ maxWidth: "40rem", margin: "0 auto", textAlign: "center" }}>
        {FALLBACK_NOTE} <Link href="/contact/">Contact us</Link>.
      </div>
    );
  }
  return (
    <>
      <div className="plans">
        {plans.map((p) => (
          <PlanCard key={p.id} p={p} featured={p.id === "growth"} trialDays={p.trial_days || trial} />
        ))}
      </div>
      <p className="compare-note" style={{ textAlign: "center" }}>
        Prices exclude VAT. AI minutes cover phone and browser calls. Free trials need no card; usage is
        uncapped during the trial so you can try everything. Plan limits marked “Unlimited” are subject to fair use.
      </p>
    </>
  );
}
