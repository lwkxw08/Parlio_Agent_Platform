import type { Metadata } from "next";
import { CtaBand, Industries, PageHero } from "@/components/blocks";

export const metadata: Metadata = {
  title: "Industries",
  description: "ParlioTec for trades and field service, clinics, estate agents, professional services and agencies.",
};

export default function IndustriesPage() {
  return (
    <>
      <PageHero
        eyebrow="Industries"
        title="Whose phone never stops"
        lead="ParlioTec is configured per business, so it speaks your customers' language, follows your rules and books into the way you already work."
      />
      <section className="section">
        <div className="wrap">
          <Industries full />
        </div>
      </section>
      <section className="section alt">
        <div className="wrap">
          <div className="section-head">
            <div className="eyebrow">Playbooks</div>
            <h2>Start from a vertical playbook</h2>
            <p className="lead">Each playbook pre-fills the greeting, FAQs, service types, urgent keywords and booking rules typical for the sector — then you tweak. The demo you can talk to on this site is the plumbing &amp; heating playbook.</p>
          </div>
        </div>
      </section>
      <CtaBand />
    </>
  );
}
