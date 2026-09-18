import type { Metadata } from "next";
import Link from "next/link";
import { CtaBand, PageHero } from "@/components/blocks";
import { Pricing } from "@/components/pricing";

export const metadata: Metadata = {
  title: "Pricing",
  description: "Simple monthly plans for UK businesses: AI minutes, SMS and numbers included, team scheduling from Growth, scheduling-tool integration on Scale, Enterprise for UK-sovereign deployment.",
};

const FAQ = [
  ["What counts as an AI minute?", "Time the assistant spends on a phone call or a browser click-to-talk conversation, rounded up per call. Text channels are metered separately as messages and each plan includes a bundle."],
  ["Is there a free trial?", "Yes — every plan has a free trial with no card required. During the trial the plan's limits don't apply, so you can try team scheduling, WhatsApp and the rest before deciding."],
  ["Can I keep my existing number?", "Yes. Most customers divert their existing landline or mobile to their ParlioTec number; you can also connect a PBX or SIP trunk on Scale and above."],
  ["What happens if I go over my minutes?", "Calls keep being answered. Extra minutes are billed at the plan's per-minute rate shown above, on your next invoice."],
  ["Can I change plans?", "Any time from Billing in the dashboard. Upgrades take effect immediately; downgrades at the end of the billing period."],
  ["Do you offer agency or reseller pricing?", "Yes — Scale includes white-label branding and agency parent accounts. Contact us for volume terms."],
  ["Where is my data stored?", "In the UK. Enterprise customers can have a dedicated UK-sovereign deployment. See the Trust page and our DPA."],
] as const;

export default function PricingPage() {
  return (
    <>
      <PageHero
        eyebrow="Pricing"
        title="Plans that grow with your team"
        lead="Straightforward monthly pricing in pounds. Every plan includes the assistant, tickets, SMS and web chat; team scheduling from Growth; your own scheduling tool from Scale."
      />
      <section className="section" style={{ paddingTop: "2rem" }}>
        <div className="wrap">
          <Pricing />
        </div>
      </section>
      <section className="section alt">
        <div className="wrap">
          <div className="section-head center"><div className="eyebrow">Questions</div><h2>Pricing FAQ</h2></div>
          <div className="faq">
            {FAQ.map(([q, a]) => (
              <details key={q}><summary>{q}</summary><p>{a}</p></details>
            ))}
          </div>
          <p style={{ textAlign: "center", marginTop: "1.8rem" }} className="muted">
            Something else? <Link href="/contact/">Ask us</Link> — a person replies within one working day.
          </p>
        </div>
      </section>
      <CtaBand title="Try it free for two weeks." body="No card, no limits during the trial, cancel in a click." />
    </>
  );
}
