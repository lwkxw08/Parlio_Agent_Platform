import type { Metadata } from "next";
import Link from "next/link";
import { LegalPage, P } from "@/components/legal";

export const metadata: Metadata = { title: "Privacy policy" };

export default function PrivacyPage() {
  return (
    <LegalPage title="Privacy policy" updated="September 2026 (draft)" current="/legal/privacy/">
      <p>
        This policy explains how <P>Legal entity name</P> (&ldquo;ParlioTec&rdquo;, &ldquo;we&rdquo;) collects and uses personal
        data. We are registered with the Information Commissioner&rsquo;s Office (ICO) under reference <P>ICO reference</P>.
        Contact: <P>privacy@parliotec.co.uk</P>, <P>registered office address</P>.
      </p>

      <h2>1. Who this policy covers</h2>
      <ul>
        <li><b>Website visitors</b> &mdash; people using parliotec.co.uk, including the &ldquo;Hear it live&rdquo; demo and the contact form.</li>
        <li><b>Customers and their team members</b> &mdash; people who create or use a ParlioTec account.</li>
        <li><b>Callers and contacts of our customers</b> &mdash; people who phone, message or chat with a business that uses ParlioTec. For this data <b>the business is the controller and we are the processor</b>; their privacy notice applies, and section 7 explains how we handle it on their behalf.</li>
      </ul>

      <h2>2. Data we collect as controller</h2>
      <table>
        <thead><tr><th>Context</th><th>Data</th><th>Purpose &amp; lawful basis</th></tr></thead>
        <tbody>
          <tr><td>Website</td><td>IP address, browser and device information, pages viewed (server logs; analytics only if you accept cookies)</td><td>Run and secure the site; legitimate interests. Analytics with consent.</td></tr>
          <tr><td>Contact form</td><td>Name, email, company, phone, your message</td><td>Reply to your enquiry; legitimate interests / steps prior to a contract.</td></tr>
          <tr><td>&ldquo;Hear it live&rdquo; demo</td><td>Your voice during the demo call, a transcript, an anonymous visitor identifier, IP address, any name you type</td><td>Provide the demo, prevent abuse (rate limits), improve the demo assistant; legitimate interests. Do not share sensitive personal data in the demo.</td></tr>
          <tr><td>Account</td><td>Name, email, phone, company, role, login and security data (2FA, sessions), billing details</td><td>Provide the Service under our contract; legal obligations (accounting); security (legitimate interests).</td></tr>
          <tr><td>Product usage</td><td>Dashboard actions, audit log, support tickets and help questions</td><td>Operate, support and improve the Service; legitimate interests.</td></tr>
          <tr><td>Marketing</td><td>Email, preferences</td><td>Product news and offers to business contacts; legitimate interests with easy opt-out, or consent where required.</td></tr>
        </tbody>
      </table>

      <h2>3. How long we keep it</h2>
      <ul>
        <li>Contact-form enquiries: <P>24 months</P> from last contact.</li>
        <li>Demo recordings and transcripts: <P>30 days</P>, then deleted.</li>
        <li>Account data: for the life of the account plus <P>30 days</P>; billing records 6 years (HMRC).</li>
        <li>Server and security logs: <P>90 days</P>.</li>
      </ul>

      <h2>4. Who we share it with</h2>
      <p>
        Service providers acting on our instructions: cloud hosting (UK regions), telephony carriers, speech-to-text,
        text-to-speech and language-model providers, email/SMS delivery, payment processing and analytics. The current list
        with locations is in the sub-processor schedule of our <Link href="/legal/dpa/">DPA</Link>. We may disclose data where
        required by law. We do not sell personal data.
      </p>

      <h2>5. International transfers</h2>
      <p>
        We host in the UK. Where a provider processes data outside the UK we rely on UK adequacy regulations or the UK
        International Data Transfer Agreement / Addendum, with additional safeguards where needed.
      </p>

      <h2>6. Your rights</h2>
      <p>
        You can ask for access to, correction or erasure of your data, object to or restrict processing, request portability,
        and withdraw consent at any time by emailing <P>privacy@parliotec.co.uk</P>. You can complain to the ICO
        (ico.org.uk, 0303 123 1113), but we would appreciate the chance to resolve concerns first.
      </p>

      <h2>7. Callers and contacts of our customers (processor role)</h2>
      <p>
        When you call, message or chat with a business using ParlioTec, we process your voice, phone number, messages,
        transcripts, summaries and any details you give (such as name, address or appointment preferences) <b>on that
        business&rsquo;s instructions</b>. Calls may be recorded and are announced where the business has enabled it. The business
        decides retention and can export or delete your data on request; please contact them directly. Our processing
        is governed by our <Link href="/legal/dpa/">Data Processing Agreement</Link> with them. We do not use this data to train
        general AI models, and platform staff access it only for support with the business&rsquo;s permission, logged in an
        audit trail.
      </p>

      <h2>8. Cookies</h2>
      <p>See our <Link href="/legal/cookies/">Cookie Policy</Link>.</p>

      <h2>9. Security</h2>
      <p>
        Encryption in transit and at rest, role-based access with two-factor authentication, audit logging, PII redaction
        options, tested backups and a documented incident process. We will notify affected customers and, where required, the
        ICO within 72 hours of becoming aware of a personal data breach.
      </p>

      <h2>10. Children</h2>
      <p>The Service and website are for businesses and are not directed at children under 16.</p>

      <h2>11. Changes</h2>
      <p>We will post updates here and, for material changes, notify account holders by email.</p>
    </LegalPage>
  );
}
