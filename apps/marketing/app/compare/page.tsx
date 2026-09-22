import type { Metadata } from "next";
import Link from "next/link";
import { CompareTable, CtaBand, PageHero } from "@/components/blocks";
import { JsonLd } from "@/components/json-ld";
import { breadcrumbLd, pageMeta } from "@/lib/seo";

export const metadata: Metadata = pageMeta("/compare/", "ParlioTec vs answering services, IVR and chatbots", "How an AI business phone system compares with call answering services, IVR menus, chatbot-only AI products and booking-link tools for UK businesses losing revenue to missed calls.");

const DIFFS = [
  { t: "Booking rules the tenant owns", b: "Slot length, on-the-hour or half-past grid, business hours with finish-by-close, travel gap, notice period, horizon and an emergency exception — set in the dashboard, enforced on every call. Booking-link tools give you fixed slots; message-taking AI doesn't book at all." },
  { t: "Service types you define", b: "Create “Boiler service – 90 min”, “Repair – 60 min”, “Quote – 30 min”. The assistant asks which, books that length and puts the service in the calendar title." },
  { t: "A real team, not one diary", b: "Team members with skills, areas, shifts and their own calendar. Pooled availability means the caller hears the earliest slot anyone suitable has; assignment is least-loaded, round-robin, nearest or preferred. The Schedule board shows every lane." },
  { t: "Your scheduling tool as the source of truth", b: "Switch the backend to ServiceM8 or a signed webhook into your own system: availability comes from it, jobs are created in it, and the calendar isn't touched." },
  { t: "Calendar events your team can act on", b: "Title “Service – Name”, phone, address (as the location), what the caller described, caller ID and a link straight to the recording and transcript." },
  { t: "Warm transfers that actually brief the human", b: "Your staff hear who's calling and why before the caller is bridged; departments have descriptions that steer routing; the human leg can be recorded and measured." },
  { t: "Booking-first when it sounds urgent", b: "“No hot water” is an appointment, not an emergency. Unless the tenant marks it so, the assistant offers the earliest slot — and asks before sending anyone to the on-call team member." },
  { t: "One assistant across voice, text and web", b: "The same knowledge and rules answer the phone, SMS, WhatsApp and the website widget — with in-page voice, so a visitor can talk without dialling." },
  { t: "Analytics you can talk to", b: "Ask in English, get a chart. Then the AI Advisor turns patterns into recommendations you can apply with one click, and a weekly digest lands in the owner's inbox." },
  { t: "Quality you can prove", b: "Every call QA-scored, every conversation searchable to the second, regression tests run before you publish a change, and a simulator to try callers against a draft." },
  { t: "Help that knows your screens", b: "“Can I block anonymous callers?” → “Yes — Assistant Studio → Call screening → tick Reject withheld…” with a chip that opens that exact section." },
  { t: "UK-first, owner-controlled", b: "Consent, redaction, retention, GDPR export/delete, audit, 2FA, approvals. Platform owners set plan caps and entitlements per tenant; agencies white-label the lot." },
];

export default function ComparePage() {
  return (
    <>
      <JsonLd data={breadcrumbLd([["Home", "/"], ["Why ParlioTec", "/compare/"]])} />
      <PageHero
        eyebrow="Why ParlioTec"
        title="Built to do the things other “AI answering” products hand back to you"
        lead="Most products in this space either take a message, play a menu, or hand out a booking link. ParlioTec was designed from the start to complete the job on the call — with your team, your rules and your tools."
      />
      <section className="section">
        <div className="wrap">
          <div className="section-head">
            <div className="eyebrow">Capability comparison</div>
            <h2>Side by side</h2>
          </div>
          <CompareTable />
        </div>
      </section>
      <section className="section alt">
        <div className="wrap">
          <div className="section-head">
            <div className="eyebrow">Differentiators</div>
            <h2>Twelve things you will struggle to find elsewhere</h2>
          </div>
          <div className="grid c2">
            {DIFFS.map((d, i) => (
              <div key={d.t} className="feature">
                <span className="tag">{String(i + 1).padStart(2, "0")}</span>
                <h3>{d.t}</h3>
                <p>{d.b}</p>
              </div>
            ))}
          </div>
          <p className="muted small" style={{ marginTop: "1.6rem" }}>
            Don&apos;t take our word for it — <Link href="/demo/">talk to the demo assistant</Link> and ask it to book a boiler service on a specific morning.
          </p>
        </div>
      </section>
      <CtaBand />
    </>
  );
}
