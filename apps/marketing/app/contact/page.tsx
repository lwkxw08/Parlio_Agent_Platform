import type { Metadata } from "next";
import Link from "next/link";
import { PageHero } from "@/components/blocks";
import { ContactForm } from "@/components/contact-form";

export const metadata: Metadata = {
  title: "Contact",
  description: "Book a personal demo, ask about plans, or talk to us about agency and enterprise deployments.",
};

export default function ContactPage() {
  return (
    <>
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
                <li>Service status: <a href="https://app.parliotec.co.uk/status">status page</a>.</li>
                <li>Email: <a href="mailto:hello@parliotec.co.uk">hello@parliotec.co.uk</a></li>
              </ul>
              <div className="notice" style={{ marginTop: "1.6rem" }}>
                <b>Company details</b><br />
                <span className="placeholder">[Legal entity name]</span>, registered in England &amp; Wales, company no.{" "}
                <span className="placeholder">[number]</span>. Registered office: <span className="placeholder">[address]</span>.
                ICO registration: <span className="placeholder">[reference]</span>.
              </div>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
