import type { Metadata } from "next";
import { pageMeta } from "@/lib/seo";
import Link from "next/link";
import { LegalPage } from "@/components/legal";

export const metadata: Metadata = pageMeta("/legal/call-recording/", "Call recording notice", "How ParlioTec records calls, the announcement callers hear, and sample wording customers can use.");

export default function CallRecordingPage() {
  return (
    <LegalPage title="Call recording & AI assistant notice" updated="September 2026" current="/legal/call-recording/">
      <p>
        This notice explains what happens when you speak to a business that uses ParlioTec, and what businesses using ParlioTec
        must tell their callers. It supplements our <Link href="/legal/privacy/">Privacy Policy</Link>.
      </p>

      <h2>For callers</h2>
      <h3>You may be speaking to an AI assistant</h3>
      <p>
        The person answering may be an automated assistant. It will say so if you ask. You can ask to be transferred to a
        member of staff, or to leave a message for a call back, at any time.
      </p>
      <h3>Recording and transcription</h3>
      <p>
        Calls are recorded and transcribed where the business has enabled this; you will hear an announcement at the start of
        the call. Recordings are used by the business to fulfil your request (for example, to book an appointment and pass the
        details to a team member), for quality and training, and to keep an accurate record. They are stored in the UK and kept
        for the period set by the business.
      </p>
      <h3>Your rights</h3>
      <p>
        The business you called is the data controller. To access, correct or delete your data, contact them directly &mdash;
        they have tools to do this quickly. If you are unhappy with how they respond you can complain to the ICO.
      </p>
      <h3>Demo calls on this website</h3>
      <p>
        Calls to the &ldquo;Hear it live&rdquo; demo are recorded by ParlioTec, kept for 30 days and used only to
        operate and improve the demo. Please do not share real personal details.
      </p>

      <h2>For businesses using ParlioTec</h2>
      <p>Under UK GDPR, the Data Protection Act 2018, PECR and Ofcom guidance you must:</p>
      <ul>
        <li>Have a lawful basis for recording (usually legitimate interests or contract) and document it; a DPIA is recommended where you handle sensitive matters.</li>
        <li>Tell callers at the start of the call that it may be recorded and, where reasonably expected, that they are talking to an automated assistant. The recording announcement is configured in Assistant Studio &rarr; Recording &amp; consent and can differ for after-hours personas.</li>
        <li>Publish a privacy notice covering call recording and AI processing (you may adapt the caller section above) and link to it from your website.</li>
        <li>Set retention in Compliance to the shortest period that meets your needs, enable PII redaction where appropriate, and use hosted payment links rather than taking card numbers by voice.</li>
        <li>Handle access and erasure requests within one month using the export and delete tools in the dashboard.</li>
        <li>Where you record the human leg of a transfer, ensure your staff are informed.</li>
      </ul>
      <p>
        Suggested announcement: &ldquo;Thanks for calling [your business name]. Calls are recorded for quality and to help us with your
        request. You&rsquo;re speaking with our virtual assistant &mdash; ask for a person at any time.&rdquo;
      </p>
      <p>Questions: privacy@parliotec.co.uk.</p>
    </LegalPage>
  );
}
