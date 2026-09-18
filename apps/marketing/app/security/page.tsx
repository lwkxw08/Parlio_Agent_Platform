import type { Metadata } from "next";
import Link from "next/link";
import { CtaBand, PageHero } from "@/components/blocks";

export const metadata: Metadata = {
  title: "Trust & security",
  description: "How ParlioTec handles caller data: UK hosting, consent, redaction, retention, GDPR rights, access control and audit.",
};

const ITEMS = [
  ["UK hosting", "Voice processing, the API, database and recordings run in UK regions. Enterprise customers can take a dedicated, UK-sovereign deployment with UK-only AI providers."],
  ["Consent & recording", "Recording announcements are configurable per business and per after-hours persona. A model call-recording notice for your own customers is provided."],
  ["PII redaction", "Card numbers and other sensitive data can be redacted from transcripts and summaries; payments use hosted links so card details never reach the assistant."],
  ["Retention", "Retention periods per data type (recordings, transcripts, messages) are set by each business and enforced by a scheduled job."],
  ["Data subject rights", "Export and erasure endpoints per caller, so you can answer GDPR requests in minutes."],
  ["Access control", "Role-based dashboard access, TOTP two-factor authentication with enforcement, active-session review and revocation; SSO / SCIM on Enterprise."],
  ["Audit trail", "Every configuration change, plan change and staff action is written to an audit log; platform staff access to a tenant is logged and visible."],
  ["Human approvals", "Actions you mark as sensitive pause for a yes/no from your team via a tap link — the assistant never guesses."],
  ["Sub-processors", "Telephony, speech, language-model and messaging providers are listed in the DPA with their roles and regions."],
  ["Reliability", "Public status page, synthetic test calls after every deployment, per-tenant health monitoring and carrier fault detection."],
] as const;

export default function SecurityPage() {
  return (
    <>
      <PageHero
        eyebrow="Trust & security"
        title="Your callers trust you. We take that seriously."
        lead="ParlioTec was designed for UK businesses from the first line of code: data stays in the UK, consent is built in, and you control what is kept and for how long."
      />
      <section className="section">
        <div className="wrap">
          <div className="grid c2">
            {ITEMS.map(([t, b]) => <div key={t} className="feature"><h3>{t}</h3><p>{b}</p></div>)}
          </div>
          <div className="notice" style={{ marginTop: "2rem" }}>
            Formal certifications (e.g. Cyber Essentials, ISO 27001) and penetration-test summaries will be listed here as they
            are completed. For security questionnaires or to report a vulnerability, email{" "}
            <a href="mailto:security@parliotec.co.uk">security@parliotec.co.uk</a>.
          </div>
          <p className="muted" style={{ marginTop: "1.2rem" }}>
            Related documents: <Link href="/legal/privacy/">Privacy policy</Link> · <Link href="/legal/dpa/">Data processing agreement</Link> ·{" "}
            <Link href="/legal/call-recording/">Call recording notice</Link> · <Link href="/legal/acceptable-use/">Acceptable use</Link>
          </p>
        </div>
      </section>
      <CtaBand />
    </>
  );
}
