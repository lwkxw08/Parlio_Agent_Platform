import type { Metadata } from "next";
import { pageMeta } from "@/lib/seo";
import Link from "next/link";
import { LegalPage } from "@/components/legal";

export const metadata: Metadata = pageMeta("/legal/terms/", "Terms of service", "The terms governing use of the ParlioTec AI business phone system: plans, billing, numbers, acceptable use, liability and termination.");

export default function TermsPage() {
  return (
    <LegalPage title="Terms of service" updated="September 2026" current="/legal/terms/">
      <p>
        These Terms of Service (&ldquo;Terms&rdquo;) govern access to and use of the ParlioTec intelligent AI-powered business phone system,
        dashboard, APIs, telephone numbers and related services (the &ldquo;Service&rdquo;) provided by KMDR Holdings Ltd,
        a company registered in England and Wales (company number 15822421) with its registered office at 124-128 City Road, London, England, EC1V 2NX{" "}
        (&ldquo;ParlioTec&rdquo;, &ldquo;we&rdquo;, &ldquo;us&rdquo;). By creating an account or using the Service you
        (&ldquo;Customer&rdquo;, &ldquo;you&rdquo;) agree to these Terms. The Service is offered to businesses only, not to
        consumers.
      </p>

      <h2>1. The Service</h2>
      <p>
        1.1 ParlioTec provides an AI-powered assistant that answers and places telephone calls and handles messages on your
        behalf, together with a dashboard for configuration, analytics and related tools. Features available to you depend on
        your plan and any trial.
      </p>
      <p>
        1.2 You configure the assistant (greeting, instructions, FAQs, booking rules, transfer destinations, integrations). You
        are responsible for the accuracy and lawfulness of that configuration and of the information the assistant is
        instructed to give.
      </p>
      <p>
        1.3 The assistant uses automated speech recognition and generative AI. Outputs may occasionally be inaccurate or
        incomplete. You must not rely on the Service for emergency services (999/112), life-safety or medical-decision
        purposes, and must tell callers how to reach emergency services where appropriate.
      </p>

      <h2>2. Accounts and trials</h2>
      <p>
        2.1 You must provide accurate account details and keep your login credentials secure. You are responsible for all
        activity under your account, including that of team members you invite.
      </p>
      <p>
        2.2 Free trials are offered at our discretion for the period shown at sign-up. At the end of the trial the Service
        continues only if you subscribe to a paid plan. We may limit trial usage to prevent abuse.
      </p>

      <h2>3. Telephone numbers and telephony</h2>
      <p>
        3.1 Numbers we provide remain allocated to our carrier partners and are licensed to you for the duration of your
        subscription. On termination we may reclaim numbers after 30 days; porting out is supported where the carrier
        permits.
      </p>
      <p>
        3.2 Where you divert your own numbers or connect your own PBX or SIP trunk, you are responsible for your carrier
        contract, call charges and the configuration of your equipment.
      </p>
      <p>
        3.3 The Service may place outbound calls and send SMS/WhatsApp messages only as configured by you. You must ensure
        you have the right to contact each recipient and comply with PECR, the TPS/CTPS and your carriers&rsquo; rules.
      </p>

      <h2>4. Fees and payment</h2>
      <p>
        4.1 Fees are as shown on the <Link href="/pricing/">pricing page</Link> or in your order, exclusive of VAT, billed monthly
        in advance for the plan and monthly in arrears for usage above the included allowances (minutes, messages, numbers).
      </p>
      <p>
        4.2 Payment is by card or direct debit through our payment provider. Invoices not paid within 14 days may result
        in suspension. We may change fees with at least 30 days&rsquo; notice; changes apply from your next billing period.
      </p>
      <p>4.3 Usage caps and feature entitlements for each plan are described in the dashboard and may be adjusted with notice.</p>

      <h2>5. Your data and content</h2>
      <p>
        5.1 You retain ownership of your configuration, recordings, transcripts, contacts and other content (&ldquo;Customer
        Data&rdquo;). You grant us a licence to process Customer Data to provide, secure and improve the Service in accordance
        with our <Link href="/legal/privacy/">Privacy Policy</Link> and <Link href="/legal/dpa/">Data Processing Agreement</Link>.
      </p>
      <p>
        5.2 You are the controller of personal data about your callers and customers; we act as processor. You are responsible
        for having a lawful basis, for giving callers appropriate notice (including of call recording &mdash; see our{" "}
        <Link href="/legal/call-recording/">Call Recording Notice</Link>), and for setting retention appropriately.
      </p>
      <p>5.3 We do not use Customer Data to train general-purpose AI models.</p>

      <h2>6. Acceptable use</h2>
      <p>
        You must comply with our <Link href="/legal/acceptable-use/">Acceptable Use Policy</Link>. We may suspend the Service
        immediately where we reasonably believe it is being used unlawfully, to send unsolicited communications, to harm
        others, or in a way that threatens the integrity of the platform.
      </p>

      <h2>7. Third-party services</h2>
      <p>
        The Service integrates with third-party services you choose to connect (for example Google Calendar, Microsoft 365,
        ServiceM8, CRMs, WhatsApp). Your use of those services is governed by their terms. We are not responsible for their
        availability or for changes they make.
      </p>

      <h2>8. Availability and support</h2>
      <p>
        8.1 We aim for high availability and publish live status at our status page. Planned maintenance will be notified in
        advance where practicable. Service-level commitments and credits, where applicable, are set out in your plan or order.
      </p>
      <p>8.2 Support is provided through the dashboard help and support desk — we respond within 48 hours; Priority support is included on eligible plans.</p>

      <h2>9. Intellectual property</h2>
      <p>
        We and our licensors own all rights in the Service, including software, voices, models and documentation. You may not
        copy, reverse-engineer, resell (other than under a written reseller agreement) or build a competing product using the
        Service. Feedback you give us may be used without restriction.
      </p>

      <h2>10. Confidentiality</h2>
      <p>Each party will keep the other&rsquo;s non-public information confidential and use it only to perform under these Terms.</p>

      <h2>11. Warranties and disclaimers</h2>
      <p>
        We warrant that the Service will perform materially as described. Otherwise, to the fullest extent permitted by law,
        the Service is provided &ldquo;as is&rdquo; and we exclude all other warranties. We do not warrant that AI outputs will
        be error-free, that every call will be answered or that bookings will always be made correctly; you should review
        activity in the dashboard.
      </p>

      <h2>12. Liability</h2>
      <p>
        12.1 Nothing in these Terms limits liability for death or personal injury caused by negligence, fraud, or anything
        that cannot be limited by law.
      </p>
      <p>
        12.2 Subject to 12.1, neither party is liable for indirect or consequential loss, loss of profit, revenue, business or
        goodwill, and each party&rsquo;s total liability in any 12-month period is limited to the fees paid by you in that
        period (or &pound;1,000 if greater).
      </p>

      <h2>13. Term and termination</h2>
      <p>
        13.1 Subscriptions renew monthly until cancelled from the dashboard; cancellation takes effect at the end of the current
        period. 13.2 Either party may terminate for material breach not remedied within 30 days of notice. 13.3 On termination
        you may export Customer Data for 30 days, after which it is deleted in line with our retention schedule.
      </p>

      <h2>14. Changes to these Terms</h2>
      <p>We may update these Terms; material changes will be notified by email or in the dashboard at least 30 days in advance.</p>

      <h2>15. General</h2>
      <p>
        These Terms are governed by the laws of England and Wales and the courts of England and Wales have exclusive
        jurisdiction. They form the entire agreement between us regarding the Service. Neither party may assign without
        consent, except to an affiliate or successor. Notices to us: legal@parliotec.co.uk.
      </p>
    </LegalPage>
  );
}
