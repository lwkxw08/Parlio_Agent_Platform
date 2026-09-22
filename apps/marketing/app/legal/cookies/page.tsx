import type { Metadata } from "next";
import { LegalPage, P } from "@/components/legal";

export const metadata: Metadata = { title: "Cookie policy" };

export default function CookiesPage() {
  return (
    <LegalPage title="Cookie policy" updated="September 2026" current="/legal/cookies/">
      <p>
        This policy explains how parliotec.com and the ParlioTec dashboard use cookies and similar technologies (including
        local storage), in line with the Privacy and Electronic Communications Regulations (PECR) and UK GDPR.
      </p>

      <h2>1. Strictly necessary</h2>
      <p>These are required for the site or dashboard to work and do not need consent.</p>
      <table>
        <thead><tr><th>Name</th><th>Set by</th><th>Purpose</th><th>Duration</th></tr></thead>
        <tbody>
          <tr><td>parliotec-demo-visitor</td><td>parliotec.com (local storage)</td><td>Anonymous identifier so the &ldquo;Hear it live&rdquo; demo can apply fair-use limits and reconnect a dropped call</td><td>Until cleared</td></tr>
          <tr><td>Session / auth cookies</td><td>app.parliotec.com</td><td>Keep you signed in to the dashboard and protect against cross-site request forgery</td><td>Session / up to <P>30 days</P></td></tr>
          <tr><td>parlio-theme, sidebar state</td><td>app.parliotec.com (local storage)</td><td>Remember display preferences</td><td>Until cleared</td></tr>
          <tr><td>__cf_bm and similar</td><td>Cloudflare</td><td>Bot protection and security for the hosting network</td><td>Up to 30 minutes</td></tr>
        </tbody>
      </table>

      <h2>2. Analytics (consent required)</h2>
      <p>
        <P>If analytics is enabled:</P> we use <P>privacy-friendly analytics provider</P> to understand which pages are useful.
        These cookies are set only after you accept them in the banner and can be withdrawn at any time via
        &ldquo;Cookie settings&rdquo; in the footer. At launch the marketing site sets <b>no analytics or advertising cookies</b>.
      </p>

      <h2>3. Third-party embeds</h2>
      <p>
        The &ldquo;Hear it live&rdquo; demo connects your browser to our real-time voice infrastructure (LiveKit, hosted by us)
        only when you press the button. No third-party advertising or social-media trackers are embedded.
      </p>

      <h2>4. Managing cookies</h2>
      <p>
        You can block or delete cookies in your browser settings; strictly necessary cookies are needed for sign-in to work.
        Guidance: <a href="https://ico.org.uk/for-the-public/online/cookies/" target="_blank" rel="noreferrer">ico.org.uk/for-the-public/online/cookies</a>.
      </p>

      <h2>5. Contact</h2>
      <p>Questions: <P>privacy@parliotec.com</P>.</p>
    </LegalPage>
  );
}
