import type { Metadata } from "next";
import Link from "next/link";
import { CtaBand, PageHero } from "@/components/blocks";
import { JsonLd } from "@/components/json-ld";
import { Pricing } from "@/components/pricing";
import { fetchSiteInfo } from "@/lib/api";
import { breadcrumbLd, faqLd, pageMeta, softwareLd } from "@/lib/seo";

export const metadata: Metadata = pageMeta("/pricing/", "Pricing for the AI business phone system", "Simple monthly plans for UK businesses with AI minutes, SMS and numbers included, free trial on every plan, team scheduling from Growth and Enterprise for UK-sovereign deployment.");

const FAQ = [
  ["What counts as an AI minute?", "Time the assistant spends on a phone call or a browser click-to-talk conversation, rounded up per call. Text channels are metered separately as messages and each plan includes a bundle."],
  ["Is there a free trial?", "Yes — every plan has a free trial with no card required. During the trial the plan's limits don't apply, so you can try team scheduling, WhatsApp and the rest before deciding."],
  ["Can I keep my existing number?", "Yes. Most customers divert their existing landline or mobile to their ParlioTec number; you can also connect a PBX or SIP trunk on Scale and above."],
  ["What happens if I go over my minutes?", "Calls keep being answered. Extra minutes are billed at the plan's per-minute rate shown above, on your next invoice."],
  ["Can I change plans?", "Any time from Billing in the dashboard. Upgrades take effect immediately; downgrades at the end of the billing period."],
  ["Do you offer agency or reseller pricing?", "Yes — Scale includes white-label branding and agency parent accounts. Contact us for volume terms."],
  ["Where is my data stored?", "In the UK. Enterprise customers can have a dedicated UK-sovereign deployment. See the Trust page and our DPA."],
] as const;

export default async function PricingPage() {
  const site = await fetchSiteInfo("build");
  const offers = (site?.plans ?? []).filter((p) => !p.enterprise && p.monthly_pence > 0).map((p) => ({ name: p.name, monthly_pence: p.monthly_pence }));
  return (
    <>
      <JsonLd data={[softwareLd(offers), faqLd(FAQ), breadcrumbLd([["Home", "/"], ["Pricing", "/pricing/"]])]} />
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
