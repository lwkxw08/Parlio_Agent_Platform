import type { Metadata } from "next";
import { CtaBand, PageHero, Steps } from "@/components/blocks";

export const metadata: Metadata = {
  title: "How it works",
  description: "How ParlioTec answers, understands, books and transfers — and how you set it up.",
};

export default function HowItWorksPage() {
  return (
    <>
      <PageHero
        eyebrow="How it works"
        title="From first ring to booked job"
        lead="Under the hood: real-time speech recognition, a language model briefed on your business, a natural British voice — and a set of tools that let it check calendars, create tickets, send texts and transfer calls."
      />
      <section className="section">
        <div className="wrap">
          <div className="section-head"><div className="eyebrow">Set-up</div><h2>Four steps</h2></div>
          <Steps />
        </div>
      </section>
      <section className="section alt">
        <div className="wrap">
          <div className="section-head"><div className="eyebrow">On the call</div><h2>What happens in those first seconds</h2></div>
          <div className="grid c3">
            {[
              ["Screen", "Withheld or blocked numbers are handled by your screening policy before a minute is used. Returning customers are recognised from their number."],
              ["Greet & consent", "Your greeting, in your chosen voice, with the recording announcement your policy requires. After-hours and holiday personas switch automatically."],
              ["Understand", "The assistant listens continuously — callers can interrupt — and works out intent: book, ask, report a fault, reach a person."],
              ["Act", "It checks live availability against your booking rules and team, books the slot, opens a ticket, sends an SMS, or warm-transfers with a spoken briefing."],
              ["Escalate safely", "Emergencies follow your rules: on-call engineer, out-of-hours booking, or a choice offered to the caller. Sensitive actions can pause for a human approval."],
              ["Wrap up", "Summary, extracted details, QA score, contact record and CRM sync happen automatically. The owner can get a text summary within seconds."],
            ].map(([t, b]) => <div key={t} className="feature"><h3>{t}</h3><p>{b}</p></div>)}
          </div>
        </div>
      </section>
      <section className="section">
        <div className="wrap">
          <div className="section-head"><div className="eyebrow">Telephony</div><h2>Three ways to connect your number</h2></div>
          <div className="grid c3">
            <div className="feature"><h3>Divert</h3><p>Forward your existing landline or mobile to your ParlioTec number — always, when busy, or after a few rings. Nothing else changes.</p></div>
            <div className="feature"><h3>New UK number</h3><p>Pick a local, 03 or 0800 number in the dashboard and publish it. Extra numbers per site or marketing channel for attribution.</p></div>
            <div className="feature"><h3>PBX / SIP trunk</h3><p>Connect your phone system as a SIP trunk with DDI routing and registration health; warm transfers ring internal extensions.</p></div>
          </div>
        </div>
      </section>
      <CtaBand />
    </>
  );
}
