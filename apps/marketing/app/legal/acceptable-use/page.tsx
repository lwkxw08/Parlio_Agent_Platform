import type { Metadata } from "next";
import Link from "next/link";
import { LegalPage, P } from "@/components/legal";

export const metadata: Metadata = { title: "Acceptable use policy" };

export default function AupPage() {
  return (
    <LegalPage title="Acceptable use policy" updated="September 2026" current="/legal/acceptable-use/">
      <p>
        This policy forms part of our <Link href="/legal/terms/">Terms of Service</Link>. It sets out what you may not do with
        ParlioTec. Because the Service places calls and sends messages on your behalf, misuse can harm members of the public and
        our carrier relationships, so we enforce it strictly.
      </p>

      <h2>1. Unlawful or harmful use</h2>
      <ul>
        <li>Any activity that breaks UK law or the law of a country you are calling or messaging.</li>
        <li>Fraud, impersonation of another business or person, or misleading callers about who they are speaking to. You must not configure the assistant to deny being automated when directly asked.</li>
        <li>Harassment, threats, abuse, hate speech or content that is sexually explicit or promotes violence.</li>
        <li>Collecting or processing special-category data (health, biometrics, etc.) without an appropriate lawful basis and safeguards, and never through the public demo.</li>
        <li>Using the Service for emergency, life-safety, medical-triage or safety-critical decisions.</li>
      </ul>

      <h2>2. Unsolicited communications</h2>
      <ul>
        <li>No cold calling or messaging of individuals who have not consented or with whom you have no existing relationship, contrary to PECR.</li>
        <li>You must screen outbound campaigns against the Telephone Preference Service / Corporate TPS and your own do-not-call list, honour STOP and opt-out requests immediately, and respect the calling windows configured in the Service.</li>
        <li>No caller-ID spoofing. Presentation numbers must be numbers you are entitled to use.</li>
        <li>No bulk or automated messaging that breaches WhatsApp, carrier or Ofcom rules.</li>
      </ul>

      <h2>3. Recording and privacy</h2>
      <ul>
        <li>You must give callers any notice of recording and processing that the law and Ofcom / ICO guidance require, and keep our <Link href="/legal/call-recording/">Call Recording Notice</Link> settings consistent with your own privacy notice.</li>
        <li>You must not attempt to identify, contact or profile callers to the public demo, or to use ParlioTec to collect payment card data outside the provided hosted payment links.</li>
      </ul>

      <h2>4. Platform integrity</h2>
      <ul>
        <li>No attempts to probe, scan, overload or bypass security or rate limits, including on the public demo.</li>
        <li>No reselling or sharing of your account, numbers or API keys except under a written reseller agreement.</li>
        <li>No use of the Service to build or train a competing AI phone-answering product.</li>
        <li>Prompt-injection or jailbreak attempts against the assistant, or instructing it to exfiltrate other tenants&rsquo; data, are prohibited.</li>
      </ul>

      <h2>5. Fair use</h2>
      <p>
        Plan allowances marked &ldquo;unlimited&rdquo; are subject to fair use consistent with a business of your size. We may
        contact you to move to a suitable plan if usage is far outside the norm.
      </p>

      <h2>6. Enforcement</h2>
      <p>
        We may warn, suspend or terminate accounts that breach this policy, and remove content or configuration causing harm,
        without refund. We cooperate with carriers, regulators and law enforcement where required. Report abuse to{" "}
        privacy@parliotec.co.uk.
      </p>
    </LegalPage>
  );
}
