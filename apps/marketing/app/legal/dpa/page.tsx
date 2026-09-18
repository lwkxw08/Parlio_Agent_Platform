import type { Metadata } from "next";
import Link from "next/link";
import { LegalPage, P } from "@/components/legal";

export const metadata: Metadata = { title: "Data processing agreement" };

export default function DpaPage() {
  return (
    <LegalPage title="Data processing agreement" updated="September 2026 (draft)" current="/legal/dpa/">
      <p>
        This Data Processing Agreement (&ldquo;DPA&rdquo;) is incorporated into the <Link href="/legal/terms/">Terms of Service</Link>{" "}
        between <P>Legal entity name</P> (&ldquo;Processor&rdquo;, &ldquo;ParlioTec&rdquo;) and the Customer
        (&ldquo;Controller&rdquo;), and reflects Article 28 of the UK GDPR and the Data Protection Act 2018.
      </p>

      <h2>1. Subject matter and duration</h2>
      <p>
        Processing of personal data about the Controller&rsquo;s callers, contacts, customers and staff for the purpose of
        providing the ParlioTec Service, for the term of the Terms plus the deletion period in section 9.
      </p>

      <h2>2. Nature and purpose of processing</h2>
      <p>
        Answering and placing telephone calls; recording and transcribing calls where enabled; handling SMS, WhatsApp and web
        chat; extracting details the Controller asks for (e.g. name, number, address, problem); creating bookings, tickets and
        contact records; sending notifications and reminders; analytics and quality scoring; synchronising data to systems the
        Controller connects.
      </p>

      <h2>3. Categories of data subjects and data</h2>
      <ul>
        <li><b>Data subjects:</b> callers, message senders and website visitors of the Controller; the Controller&rsquo;s customers, prospects, staff and team members.</li>
        <li><b>Data:</b> voice recordings and transcripts; phone numbers and caller ID; names, addresses, email; appointment details; free-text descriptions of the caller&rsquo;s request; message content; dashboard user details. Special-category data may be incidentally captured in speech (e.g. health details given by a caller); the Controller must set redaction and retention accordingly.</li>
      </ul>

      <h2>4. Controller instructions</h2>
      <p>
        The Processor processes personal data only on the Controller&rsquo;s documented instructions, which are the Terms, this
        DPA, and the configuration the Controller sets in the dashboard (recording, consent wording, retention, redaction,
        integrations, outbound policies). The Processor will inform the Controller if an instruction appears to infringe data
        protection law.
      </p>

      <h2>5. Confidentiality and personnel</h2>
      <p>Staff with access to personal data are bound by confidentiality, trained, and access is role-based, two-factor protected and logged. Support access to a tenant&rsquo;s data occurs only with the Controller&rsquo;s permission and is recorded in the audit log the Controller can view.</p>

      <h2>6. Security measures</h2>
      <ul>
        <li>Encryption in transit (TLS 1.2+) and at rest; per-tenant isolation enforced at the database layer (row-level security).</li>
        <li>UK-region hosting for compute, storage and recordings; optional UK-sovereign single-tenant deployment.</li>
        <li>Configurable PII redaction in transcripts and summaries; hosted payment links so card data is never processed by the assistant.</li>
        <li>Retention policies enforced automatically; export and erasure endpoints per data subject.</li>
        <li>Audit logging, vulnerability management, tested backups, incident response process.</li>
      </ul>

      <h2>7. Sub-processors</h2>
      <p>
        The Controller gives general authorisation to the sub-processors listed below. We will give at least 30 days&rsquo; notice
        of additions or replacements by email or dashboard announcement; the Controller may object on reasonable grounds and,
        if unresolved, terminate the affected service.
      </p>
      <table>
        <thead><tr><th>Provider</th><th>Role</th><th>Location</th></tr></thead>
        <tbody>
          <tr><td><P>Cloud host, e.g. DigitalOcean / AWS</P></td><td>Compute, database, backups</td><td>London, UK</td></tr>
          <tr><td>Cloudflare</td><td>Content delivery, website hosting, object storage (recordings)</td><td>UK / EU with UK jurisdiction setting</td></tr>
          <tr><td>Telnyx</td><td>Telephony carrier, SMS, numbers</td><td>UK / EU</td></tr>
          <tr><td>LiveKit (self-hosted)</td><td>Real-time media</td><td>UK</td></tr>
          <tr><td>Deepgram</td><td>Speech-to-text</td><td><P>region</P></td></tr>
          <tr><td>OpenAI / Anthropic / <P>as configured</P></td><td>Language model (no training on Customer Data)</td><td><P>region</P></td></tr>
          <tr><td>Cartesia / ElevenLabs</td><td>Text-to-speech</td><td><P>region</P></td></tr>
          <tr><td>Resend</td><td>Transactional email</td><td><P>region</P></td></tr>
          <tr><td>Meta (WhatsApp Business)</td><td>WhatsApp channel, where enabled by Controller</td><td>Global</td></tr>
          <tr><td>Stripe</td><td>Payments and billing</td><td>UK / EU / US</td></tr>
        </tbody>
      </table>
      <p>Integrations the Controller connects itself (Google, Microsoft, ServiceM8, CRMs) are independent controllers/processors under the Controller&rsquo;s own agreements with them.</p>

      <h2>8. International transfers</h2>
      <p>Personal data is hosted in the UK. Where a sub-processor processes data outside the UK, transfers rely on UK adequacy regulations or the UK IDTA / Addendum with supplementary measures. Controllers requiring no international transfers may select UK-only providers on Enterprise.</p>

      <h2>9. Deletion and return</h2>
      <p>The Controller can export data at any time. On termination the Controller has <P>30 days</P> to export, after which personal data is deleted from live systems within 30 days and from backups within <P>90 days</P>, save where retention is required by law.</p>

      <h2>10. Assistance</h2>
      <p>The Processor will assist the Controller with data subject requests (through built-in export/erasure tools), DPIAs, and consultations with the ICO, and will make available information needed to demonstrate compliance, including permitting audits on reasonable notice no more than once a year, or on a regulator&rsquo;s request.</p>

      <h2>11. Personal data breaches</h2>
      <p>The Processor will notify the Controller without undue delay and in any event within 48 hours of becoming aware of a personal data breach affecting the Controller&rsquo;s data, with the information the Controller needs to meet its own 72-hour obligation.</p>

      <h2>12. Liability and precedence</h2>
      <p>Liability under this DPA is subject to the limits in the Terms. In case of conflict, this DPA prevails over the Terms with respect to data protection.</p>

      <h2>Signature</h2>
      <p>This DPA is accepted electronically when the Controller accepts the Terms. A countersigned copy is available on request from <P>legal@parliotec.co.uk</P>.</p>
    </LegalPage>
  );
}
