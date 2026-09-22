import type { Metadata } from "next";
import Link from "next/link";
import { PageHero } from "@/components/blocks";
import { ContactForm } from "@/components/contact-form";
import { JsonLd } from "@/components/json-ld";
import { breadcrumbLd, pageMeta } from "@/lib/seo";

export const metadata: Metadata = pageMeta("/contact/", "Contact ParlioTec", "Book a personal demo of the ParlioTec AI business phone system, ask about plans, or talk to us about agency, reseller and enterprise deployments.");

export default function ContactPage() {
  return (
    <>
      <JsonLd data={breadcrumbLd([["Home", "/"], ["Contact", "/contact/"]])} />
      <PageHero
        eyebrow="Contact"
        title="Talk to a person"
        lead="Book a personal demo on your own scenarios, ask about Enterprise or reseller terms, or just check whether ParlioTec fits."
      />
      <section className="section" style={{ paddingTop: "2rem" }}>
        <div className="wrap">
          <div className="split" style={{ alignItems: "start" }}>
            <div className="feature" style={{ padding: "2rem" }}>
              <ContactForm />
            </div>
            <div>
              <h3>Other ways in</h3>
              <ul className="demo-list" style={{ marginTop: "0.8rem" }}>
                <li>Try it without talking to us: <Link href="/demo/">hear the assistant live</Link>.</li>
                <li>Existing customer? Press <b>?</b> on any dashboard screen for guided help, or open a ticket from Support.</li>
                <li>Service status: <a href="https://app.parliotec.com/status">status page</a>.</li>
                <li>Email: <a href="mailto:hello@parliotec.com">hello@parliotec.com</a></li>
              </ul>
              <div className="notice" style={{ marginTop: "1.6rem" }}>
                <b>Company details</b><br />
                KMDR Holdings Ltd, registered in England &amp; Wales, company no.{" "}
                15822421. Registered office: 124-128 City Road, London, England, EC1V 2NX.
                ICO registration: <strong>00015465911</strong>.
              </div>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
