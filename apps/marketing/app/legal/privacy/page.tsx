import type { Metadata } from "next";
import { pageMeta } from "@/lib/seo";
import Link from "next/link";
import { LegalPage } from "@/components/legal";

export const metadata: Metadata = pageMeta("/legal/privacy/", "Privacy policy", "How KMDR Holdings Ltd (ParlioTec) collects, uses and protects personal data, including caller data, Google and Microsoft calendar data and your rights under UK GDPR.");

export default function PrivacyPage() {
  return (
    <LegalPage title="Privacy policy" updated="September 2026" current="/legal/privacy/">
      <p>
        This policy explains how KMDR Holdings Ltd (&ldquo;ParlioTec&rdquo;, &ldquo;we&rdquo;) collects and uses personal
        data. We are registered with the Information Commissioner&rsquo;s Office (ICO) under registration number <strong>00015465911</strong>.
        Contact: privacy@parliotec.co.uk, 124-128 City Road, London, England, EC1V 2NX.
      </p>

      <h2>1. Who this policy covers</h2>
      <ul>
        <li><b>Website visitors</b> &mdash; people using parliotec.com, including the &ldquo;Hear it live&rdquo; demo and the contact form.</li>
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
        <li>Contact-form enquiries: 24 months from last contact.</li>
        <li>Demo recordings and transcripts: 30 days, then deleted.</li>
        <li>Account data: for the life of the account plus 30 days; billing records 6 years (HMRC).</li>
        <li>Server and security logs: 90 days.</li>
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
        and withdraw consent at any time by emailing privacy@parliotec.co.uk. You can complain to the ICO
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

      <h2 id="google">8. Google user data (Google Calendar integration)</h2>
      <p>
        The ParlioTec application (the dashboard at app.parliotec.com) offers an optional connection to Google Calendar
        through Google Sign-In / OAuth 2.0. This section describes, for the purposes of the Google API Services User Data
        Policy, exactly what Google user data ParlioTec accesses, how it is used, stored, shared and deleted. Nothing in
        this section applies until a customer chooses to click &ldquo;Connect Google Calendar&rdquo; and grants consent on
        Google&rsquo;s consent screen.
      </p>
      <h3>8.1 What Google user data we request</h3>
      <table>
        <thead><tr><th>Google scope</th><th>Data it gives access to</th><th>Why ParlioTec needs it</th></tr></thead>
        <tbody>
          <tr><td><code>https://www.googleapis.com/auth/calendar.readonly</code></td><td>The list of calendars on the account and the free/busy status of events on the calendar the customer selects</td><td>To let the customer choose which calendar to book into, and to work out which appointment slots are free when a caller asks to book</td></tr>
          <tr><td><code>https://www.googleapis.com/auth/calendar.events</code></td><td>Create, update and delete events</td><td>To create the appointment event when a caller books, and to move or cancel that event if the caller reschedules or cancels</td></tr>
          <tr><td><code>openid</code>, <code>email</code></td><td>The Google account&rsquo;s email address and account identifier</td><td>To label the connection in the dashboard (&ldquo;Connected as name@example.com&rdquo;) and to match a refreshed token to the right connection</td></tr>
        </tbody>
      </table>
      <h3>8.2 How we use it</h3>
      <ul>
        <li><b>Availability:</b> when a caller asks for an appointment, ParlioTec queries the Google Calendar free/busy endpoint for the selected calendar within the customer&rsquo;s configured booking window (for example the next 14 days) and offers only the free slots that fit the customer&rsquo;s booking rules.</li>
        <li><b>Booking:</b> when the caller accepts a slot, ParlioTec creates a calendar event on that calendar containing the appointment time, the service requested, and the caller details the customer has asked us to capture (typically name, phone number, address and a short description of the request).</li>
        <li><b>Changes:</b> if the caller reschedules or cancels through ParlioTec, we update or delete that same event. We never modify events that ParlioTec did not create.</li>
        <li><b>Dashboard:</b> bookings ParlioTec created are shown to the customer&rsquo;s team in the Bookings and Schedule screens of the dashboard.</li>
      </ul>
      <p>
        We read only the start and end times and the free/busy status of the customer&rsquo;s existing events. We do not read,
        display or store the titles, descriptions, attendees, locations or attachments of any event that ParlioTec did not
        itself create.
      </p>
      <h3>8.3 What we store, and for how long</h3>
      <ul>
        <li>The OAuth refresh token and short-lived access token, encrypted at rest (authenticated AES encryption) in our UK-hosted database, used solely to make the calendar requests above on the customer&rsquo;s behalf.</li>
        <li>The connected Google account email address and the identifier of the calendar the customer selected.</li>
        <li>The identifiers, times and booking details of events that ParlioTec created, so they can be shown in the dashboard and updated or cancelled later.</li>
        <li>Free/busy results are used in memory to calculate available slots and are not retained after the call or booking session ends.</li>
      </ul>
      <p>
        Tokens are deleted immediately when the customer disconnects the calendar or when the account is closed. Booking
        records are kept for the life of the customer&rsquo;s account plus 30 days (section 3), or for the retention period
        the customer configures, whichever is shorter.
      </p>
      <h3>8.4 Sharing of Google user data</h3>
      <p>
        We do not sell Google user data. We do not share it with advertisers, data brokers or any other third party. Google
        user data is transmitted only between ParlioTec&rsquo;s servers and Google&rsquo;s APIs, except that appointment details
        (time, service, caller details) are, on the customer&rsquo;s instruction, sent to the customer&rsquo;s own connected
        tools (for example a CRM or field-service system) and stored by our UK cloud hosting provider as part of
        running the Service. We do not use Google user data for advertising, for profiling, for market research, or to
        develop, improve or train generalised artificial-intelligence or machine-learning models.
      </p>
      <h3 id="google-ai">8.5 AI and machine-learning services</h3>
      <p>
        ParlioTec uses third-party AI services (speech recognition, large language models and text-to-speech) to hold the
        conversation with a caller. Google user data is not sent to these services in raw form. The only Google-derived
        data an AI model ever receives is the list of free appointment slots computed from the free/busy query (dates and
        times), so the assistant can offer them to the caller. Event titles, descriptions, attendees, the connected account
        email and OAuth tokens are never passed to any AI service. The appointment the caller agrees to is created in
        Google Calendar by ParlioTec&rsquo;s own servers, not by an AI provider.
      </p>
      <p>
        The AI providers we use are OpenAI (OpenAI API platform, for the language model), Deepgram (Deepgram API, for
        speech recognition) and Cartesia (Cartesia API, for text-to-speech). We access each directly through its paid
        business API, under terms that prohibit the use of API inputs and outputs to train or improve their generalised
        models, with no opt-in to data sharing; we do not use aggregators, gateways or model hubs, and we do not operate
        self-hosted or offline models. We do not use raw, aggregated or anonymised Google user data to develop, improve or
        train any AI or machine-learning model, whether our own or a third party&rsquo;s. We will update this list before
        adding or replacing a provider.
      </p>
      <h3>8.6 Human access</h3>
      <p>
        ParlioTec staff do not read Google user data except (a) with the customer&rsquo;s explicit permission to resolve a
        support request, (b) where necessary for security purposes such as investigating abuse, (c) to comply with
        applicable law, or (d) where the data has been aggregated and anonymised for internal operations. All staff access
        is recorded in an audit log the customer can view.
      </p>
      <h3 id="limited-use">8.7 Limited Use disclosure</h3>
      <p>
        ParlioTec&rsquo;s use and transfer to any other app of information received from Google APIs will adhere to the{" "}
        <a href="https://developers.google.com/terms/api-services-user-data-policy#additional_requirements_for_specific_api_scopes" target="_blank" rel="noreferrer">
          Google API Services User Data Policy
        </a>
        , including the Limited Use requirements. The use of raw or derived user data received from Workspace APIs will
        adhere to the Google User Data Policy, including the Limited Use requirements.
      </p>
      <h3>8.8 Revoking access and deleting your data</h3>
      <p>
        Customers can disconnect Google Calendar at any time from <b>Integrations &rarr; Calendar &rarr; Disconnect</b> in the
        dashboard, which deletes the stored tokens immediately, or revoke ParlioTec&rsquo;s access from their Google Account at{" "}
        <a href="https://myaccount.google.com/permissions" target="_blank" rel="noreferrer">myaccount.google.com/permissions</a>.
        Events ParlioTec created remain in the customer&rsquo;s Google Calendar unless they delete them. To have all stored
        booking records deleted, customers can use the account-deletion option in Settings &rarr; Compliance or email{" "}
        privacy@parliotec.co.uk; we complete deletion within 30 days.
      </p>

      <h2 id="microsoft">9. Microsoft 365 / Outlook calendar data</h2>
      <p>
        Customers may alternatively connect a Microsoft 365 or Outlook.com calendar. We request the Microsoft Graph
        permissions <code>Calendars.ReadWrite</code> (read free/busy and create, update or delete the appointments we book)
        and <code>User.Read</code> (the account email address, to label the connection). The same use, storage, sharing,
        human-access and deletion commitments in section 8 apply. Access can be revoked from the dashboard or from the
        customer&rsquo;s Microsoft account settings.
      </p>

      <h2>10. Cookies</h2>
      <p>See our <Link href="/legal/cookies/">Cookie Policy</Link>.</p>

      <h2>11. Security</h2>
      <p>
        Encryption in transit and at rest, role-based access with two-factor authentication, audit logging, PII redaction
        options, tested backups and a documented incident process. We will notify affected customers and, where required, the
        ICO within 72 hours of becoming aware of a personal data breach.
      </p>

      <h2>12. Children</h2>
      <p>The Service and website are for businesses and are not directed at children under 16.</p>

      <h2>13. Changes</h2>
      <p>We will post updates here and, for material changes, notify account holders by email.</p>
    </LegalPage>
  );
}
