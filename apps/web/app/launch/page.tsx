export const dynamic = "force-dynamic";

type Carrier = { name: string; on: string; off: string; note?: string };

const UK: Carrier[] = [
  { name: "BT landline (Call Diversion)", on: "*21*<number>#", off: "#21#", note: "Divert on no answer: *61*<number>#  ·  Divert when busy: *67*<number>#. Call Diversion must be enabled on the line (free on most BT plans)." },
  { name: "BT Cloud Voice / Digital Voice", on: "*21*<number>#", off: "#21#", note: "Or set the forward in the BT Business app → Call settings → Call forwarding." },
  { name: "Virgin Media", on: "*21*<number>#", off: "#21#", note: "No answer: *61*<number>#. Busy: *67*<number>#." },
  { name: "Sky Talk", on: "*21*<number>#", off: "#21#" },
  { name: "TalkTalk", on: "*21*<number>#", off: "#21#", note: "Enable Call Divert in My Account first." },
  { name: "EE / O2 / Vodafone / Three (mobile)", on: "**21*<number>#", off: "##21#", note: "No answer: **61*<number>*11*<seconds>#. Busy: **67*<number>#. Unreachable: **62*<number>#." },
  { name: "3CX / FreePBX / Horizon / RingCentral (PBX)", on: "Set an inbound rule or hunt-group overflow to the ParlioTec number", off: "Remove the rule", note: "Better: connect the PBX directly over SIP (coming in the BYO SIP release) so calls arrive with caller ID and without PSTN charges." },
];

const US: Carrier[] = [
  { name: "AT&T", on: "*72 then <number>", off: "*73", note: "Busy: *90 <number> (off *91). No answer: *92 <number> (off *93)." },
  { name: "Verizon", on: "*72 <number>", off: "*73", note: "No answer: *71 <number>." },
  { name: "T-Mobile", on: "**21*<number>#", off: "##21#", note: "No answer: **61*<number>#. Busy: **67*<number>#." },
  { name: "Spectrum / Comcast Xfinity / Cox", on: "*72 <number>", off: "*73", note: "Some plans use 72# and 73#." },
  { name: "Google Voice", on: "Settings → Calls → Forward calls to linked number", off: "Unlink the number" },
  { name: "RingCentral / Dialpad / Zoom Phone", on: "Set the after-hours or overflow forwarding rule to the ParlioTec number", off: "Remove the rule" },
];

function Table({ rows, number }: { rows: Carrier[]; number: string }) {
  const sub = (s: string) => s.replace("<number>", number);
  return (
    <table>
      <thead><tr><th>Provider</th><th>Forward all calls</th><th>Stop forwarding</th><th>Notes</th></tr></thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.name}>
            <td>{r.name}</td>
            <td><code>{sub(r.on)}</code></td>
            <td><code>{r.off}</code></td>
            <td className="small muted">{r.note ? sub(r.note) : ""}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default async function Launch({ searchParams }: { searchParams: Promise<{ region?: string }> }) {
  const sp = await searchParams;
  const region = sp.region === "us" ? "us" : "uk";
  const number = "<your ParlioTec number>";

  return (
    <>
      <h1>How to launch</h1>
      <p className="muted small">Your ParlioTec number is assigned when telephony is connected (Telnyx UK/US numbers). Until then use the placeholder below.</p>
      <div className="chips" style={{ marginBottom: "1rem" }}>
        <a href="/launch?region=uk" className={region === "uk" ? "active" : ""}>United Kingdom</a>
        <a href="/launch?region=us" className={region === "us" ? "active" : ""}>United States</a>
      </div>

      <div className="section">
        <h2>1. Test the assistant</h2>
        <p className="hint">Call your ParlioTec number directly from a mobile. Check the greeting, ask a few FAQs, and try “Can I speak to someone?” to test transfer or ticket behaviour. Review the call under Calls and leave feedback on anything that was wrong.</p>
      </div>

      <div className="section">
        <h2>2. Choose how calls reach ParlioTec</h2>
        <ul className="small">
          <li><b>Forward everything</b> — your existing number diverts to ParlioTec; the assistant answers every call and transfers to you when needed.</li>
          <li><b>Overflow only</b> — divert on <i>no answer</i> / <i>busy</i>, so ParlioTec picks up only what you miss (most popular for small teams).</li>
          <li><b>Out of hours</b> — set a time-based rule on your PBX or mobile so ParlioTec covers evenings and weekends.</li>
          <li><b>Connect your PBX over SIP</b> — no forwarding codes, caller ID preserved, transfers to extensions. Available in the BYO SIP release.</li>
        </ul>
      </div>

      <div className="section">
        <h2>3. Forwarding codes — {region === "uk" ? "UK" : "US"}</h2>
        <p className="hint">Dial the code from the phone or line you want to forward, then hang up when you hear the confirmation tone. Replace <code>{number}</code> with your ParlioTec number{region === "uk" ? " (dial as 0… or +44…)" : " (10 digits)"}.</p>
        <Table rows={region === "uk" ? UK : US} number={number} />
        <p className="small muted" style={{ marginTop: 8 }}>
          Codes vary by plan; if a code fails, use your provider&apos;s app/portal or call them. {region === "uk" ? "With the PSTN switch-off (completed by Jan 2027) most UK lines are now VoIP; your provider's portal usually has a “Call forwarding” setting." : "Some carriers charge for forwarded minutes — check your plan."}
        </p>
      </div>

      <div className="section">
        <h2>4. Verify</h2>
        <p className="hint">Call your business number from another phone. ParlioTec should answer within a second. If the caller ID shown in Calls is your own number rather than the caller&apos;s, your provider is masking CLI on diverted calls — ask them to enable “pass original caller ID” or connect via SIP instead.</p>
      </div>

      <div className="section">
        <h2>5. Tell your customers</h2>
        <p className="hint">Add “Calls may be answered by our AI assistant and recorded” to your website and voicemail greeting. The consent announcement is configured under Assistant → Recording.</p>
      </div>
    </>
  );
}
