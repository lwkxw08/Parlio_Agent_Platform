import type { Metadata } from "next";
import { CtaBand, PageHero, Steps } from "@/components/blocks";
import { JsonLd } from "@/components/json-ld";
import { breadcrumbLd, faqLd, pageMeta } from "@/lib/seo";

export const metadata: Metadata = pageMeta("/how-it-works/", "How the AI business phone system works", "How ParlioTec answers, understands, books and transfers calls, how your number connects (forward, SIP or new number) and how set-up takes an afternoon.");

const FAQ = [
  ["How quickly does the AI answer a call?", "On the first ring, every time, 24/7. There is no queue, no hold music and no voicemail; callers speak to the assistant straight away, whether it is 9am on a Monday or 11pm on a bank holiday."],
  ["Will callers know they are talking to an AI?", "Yes: the greeting and recording announcement you configure make it clear. Most callers simply want a booking or an answer, and the natural British voice keeps the conversation flowing without the robotic menus of an IVR."],
  ["What happens to a call the AI can't handle?", "It follows your rules: a warm transfer to the right person with a private briefing, a callback ticket with everything captured, or an out-of-hours booking. A promised transfer is never left hanging; if nobody picks up, the caller is told and a callback is logged."],
  ["Does it book straight into my calendar?", "Yes. It checks live availability against your booking rules and your team's Google or Microsoft calendars (or your scheduling tool), offers real slots and writes the appointment with the job details attached."],
  ["Do I need to change my phone number?", "No. Most businesses divert their existing landline or mobile to their ParlioTec number, always or only when busy or unanswered. You can also connect a PBX/SIP trunk or take a new UK number."],
  ["How long does set-up take?", "About an afternoon: describe your business, pick a voice, connect a calendar and your number, then make a one-click test call. Vertical playbooks pre-fill the FAQs, service types and booking rules for your sector."],
] as const;

export default function HowItWorksPage() {
  return (
    <>
      <JsonLd data={[faqLd(FAQ), breadcrumbLd([["Home", "/"], ["How it works", "/how-it-works/"]])]} />
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
              ["Escalate safely", "Emergencies follow your rules: on-call team member, out-of-hours booking, or a choice offered to the caller. Sensitive actions can pause for a human approval."],
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
      <section className="section alt">
        <div className="wrap">
          <div className="section-head center"><div className="eyebrow">Questions</div><h2>How it works FAQ</h2></div>
          <div className="faq">
            {FAQ.map(([q, a]) => (
              <details key={q}><summary>{q}</summary><p>{a}</p></details>
            ))}
          </div>
        </div>
      </section>
      <CtaBand />
    </>
  );
}
