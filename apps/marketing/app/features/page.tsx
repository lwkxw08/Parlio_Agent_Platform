import type { Metadata } from "next";
import { CtaBand, FeatureGrid, PageHero } from "@/components/blocks";
import { PILLARS } from "@/lib/content";

export const metadata: Metadata = {
  title: "Platform",
  description: "Everything ParlioTec does: AI phone answering, rules-based booking, team scheduling, scheduling-tool integration, warm transfers, omnichannel inbox, analytics, QA and UK-first compliance.",
};

const GROUPS: { id: string; title: string; blurb: string; idx: number[] }[] = [
  { id: "answer", title: "Answer & resolve", blurb: "The conversation itself — voice, understanding, transfers and follow-through.", idx: [0, 4, 5, 6] },
  { id: "book", title: "Book & schedule", blurb: "Where ParlioTec goes far beyond message-taking: your rules, your team, your tools.", idx: [1, 2, 3] },
  { id: "measure", title: "Measure & improve", blurb: "Know what callers want, see what's coming, prove the value — and let the assistant get better with every call.", idx: [7, 12, 8] },
  { id: "scale", title: "Run at scale", blurb: "Several sites or brands, your CRM stack, and the controls a regulated business needs.", idx: [9, 10, 11] },
];

export default function FeaturesPage() {
  return (
    <>
      <PageHero
        eyebrow="Platform"
        title="One assistant. The whole front office."
        lead="ParlioTec is not a chatbot bolted onto a phone line. It is an intelligent AI-powered business phone system: front desk, booking desk, dispatcher and analyst in one — configured in plain English from one dashboard."
      />
      {GROUPS.map((g, i) => (
        <section key={g.title} id={g.id} className={`section ${i % 2 ? "alt" : ""}`}>
          <div className="wrap">
            <div className="section-head">
              <div className="eyebrow">{g.title}</div>
              <h2>{g.blurb}</h2>
            </div>
            <FeatureGrid items={g.idx.map((n) => PILLARS[n])} cols={g.idx.length >= 4 ? 4 : 3} />
          </div>
        </section>
      ))}
      <section className="section">
        <div className="wrap">
          <div className="section-head">
            <div className="eyebrow">Also included</div>
            <h2>The details that make it feel finished</h2>
          </div>
          <div className="grid c3">
            {[
              ["Assistant Studio", "Greeting, instructions, FAQs, rules, business hours, blocked numbers, languages — with “Ask AI to draft” and version history / rollback."],
              ["Ask ParlioTec help", "A ? on every screen with that page's guide and a Q&A box grounded on the real documentation, linking to the exact setting."],
              ["Onboarding wizard", "Paste your website, pick a voice, connect your calendar and place a test call in minutes; a setup checklist tracks the rest."],
              ["Call screening", "Reject withheld numbers, screen unknown callers, flag spam — before a minute is spent."],
              ["Numbers & telephony", "New UK numbers by area code, divert your existing line, or connect a PBX / SIP trunk with DDI routing and registration health."],
              ["Live monitoring", "Watch transcripts in real time, listen in, whisper to the assistant or take over the call yourself."],
              ["Human approvals", "Sensitive actions pause for a yes/no from your team by SMS or Slack tap link."],
              ["Payments", "Send a hosted payment link mid-call by SMS; card details never touch the assistant."],
              ["Status & health", "Public status page, per-tenant health scores, synthetic test calls after every deploy."],
            ].map(([t, b]) => (
              <div key={t} className="feature"><h3>{t}</h3><p>{b}</p></div>
            ))}
          </div>
        </div>
      </section>
      <CtaBand title="See it on your own calls." body="Start a trial and run a test call in minutes — or talk to the demo assistant now." />
    </>
  );
}
