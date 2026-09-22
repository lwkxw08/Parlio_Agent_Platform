import type { Metadata } from "next";
import Link from "next/link";
import { CtaBand } from "@/components/blocks";
import { HearItLive } from "@/components/demo";
import { JsonLd } from "@/components/json-ld";
import { breadcrumbLd, pageMeta } from "@/lib/seo";

export const metadata: Metadata = pageMeta("/demo/", "Hear the AI business phone system live", "Listen to a real ParlioTec call or talk to the assistant in your browser: no phone, no sign-up. Hear how it answers, books an appointment and handles an emergency.");

export default function DemoPage() {
  return (
    <>
      <JsonLd data={breadcrumbLd([["Home", "/"], ["Hear it live", "/demo/"]])} />
      <section className="page-hero">
        <div className="wrap">
          <div className="eyebrow">Hear it live</div>
          <h1>Talk to ParlioTec. Right now, in your browser.</h1>
          <p className="lead">
            No form, no sales call. Press the button, allow your microphone, and you&apos;re speaking to a real ParlioTec
            assistant set up as a demo plumbing &amp; heating company.
          </p>
        </div>
      </section>
      <section className="section" style={{ paddingTop: "2rem" }}>
        <div className="wrap">
          <HearItLive compact />
        </div>
      </section>
      <section className="section alt">
        <div className="wrap">
          <div className="section-head"><div className="eyebrow">What you&apos;re hearing</div><h2>Everything in the demo is standard configuration</h2></div>
          <div className="grid c3">
            <div className="feature"><h3>Booking rules</h3><p>60-minute slots on the hour or half-past within business hours, a travel gap between jobs, and a genuine-emergency exception.</p></div>
            <div className="feature"><h3>Service types</h3><p>Boiler service, repair and quote — each with its own length. Ask for one and listen for the assistant offering the right duration.</p></div>
            <div className="feature"><h3>Emergency handling</h3><p>Say you can smell gas and it will follow the safety script and offer the on-call team member; say “no hot water” and it books the earliest slot instead.</p></div>
          </div>
          <p className="muted small" style={{ marginTop: "1.4rem" }}>
            Demo calls are limited to three minutes and a monthly allowance, and are recorded so we can keep the demo assistant
            accurate. Please don&apos;t give real personal details. See the <Link href="/legal/privacy/">privacy policy</Link>.
          </p>
        </div>
      </section>
      <CtaBand title="Want it answering your phone by tonight?" body="Start a trial; the onboarding wizard drafts your assistant from your website in minutes." />
    </>
  );
}
