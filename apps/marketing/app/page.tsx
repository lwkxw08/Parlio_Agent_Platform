import Image from "next/image";
import Link from "next/link";
import { CompareTable, CtaBand, FeatureGrid, Industries, Steps } from "@/components/blocks";
import { HearItLive } from "@/components/demo";
import { Reveal } from "@/components/reveal";
import { signupUrl } from "@/lib/api";
import { COMPARE, PILLARS } from "@/lib/content";

const HOME_PILLARS = PILLARS.slice(0, 6);
const HOME_COMPARE = COMPARE.slice(0, 8);

export default function Home() {
  return (
    <>
      <section className="hero">
        <div className="wrap">
          <div>
            <span className="pill"><span className="dot" />Intelligent AI-powered business phone system · built in the UK</span>
            <h1>
              The business phone system that <span className="grad-text">books the job</span>, not just the message
            </h1>
            <p className="lead">
              ParlioTec answers every call in a natural British voice, books real appointments across your whole team&apos;s
              calendars or scheduling tool, transfers warm to a person when it matters, handles SMS, WhatsApp and web
              chat with the same brain — and learns from every conversation to run your business better.
            </p>
            <div className="actions">
              <a className="btn primary lg" href={signupUrl}>Start free trial</a>
              <Link className="btn secondary lg" href="/demo/">Hear it live — talk to it now</Link>
            </div>
            <div className="proof">
              <span>No card for the trial</span>
              <span>Keep your number</span>
              <span>Set up in an afternoon</span>
              <span>UK data handling</span>
            </div>
          </div>
          <div className="mock">
            <div className="card">
              <div className="call-head">
                <div className="avatar">G</div>
                <div>
                  <b>Gemma · ParlioTec assistant</b>
                  <div className="muted small">Incoming call · 07700 900123</div>
                </div>
                <span className="pill" style={{ marginLeft: "auto" }}><span className="dot" />Live</span>
              </div>
              <div className="bubbles">
                <div className="bubble ai">Good morning, Northside Heating, Gemma speaking. How can I help?</div>
                <div className="bubble caller">Hi — my boiler&apos;s stopped working, can someone come out this week?</div>
                <div className="bubble ai">Sorry to hear that. I can book a repair visit — that&apos;s an hour. Tom&apos;s free Thursday at 10 or Friday at 2. Which suits?</div>
                <div className="bubble caller">Thursday at 10 please.</div>
                <div className="bubble ai">Booked with Tom, Thursday 10 to 11. I&apos;ll text you a confirmation now — what&apos;s the postcode?</div>
              </div>
              <div className="booked">✓ Booked · Tom · Thu 10:00 · Boiler repair (60 min) · SMS sent</div>
            </div>
            <div className="float a"><span>⚡</span><div><b>Barge-in</b>callers can interrupt</div></div>
            <div className="float b"><span>📅</span><div><b>4 team members</b>pooled availability</div></div>
          </div>
        </div>
      </section>

      <div className="wrap">
        <div className="stats">
          <div className="stat"><b>24/7</b><span>Every call answered, including evenings, weekends and bank holidays</span></div>
          <div className="stat"><b>0 holds</b><span>No queues or menus — callers speak, and can interrupt, like talking to a person</span></div>
          <div className="stat"><b>1 → 25+</b><span>Bookable team members per account with pooled availability</span></div>
          <div className="stat"><b>3 channels</b><span>Phone, SMS/WhatsApp and web chat handled by the same assistant</span></div>
        </div>
      </div>

      <section className="section">
        <div className="wrap">
          <div className="section-head center">
            <div className="eyebrow">What makes ParlioTec different</div>
            <h2>Most AI phone answering takes a message. ParlioTec runs your diary.</h2>
            <p className="lead">
              Tenant-defined booking rules, service types, a whole team with pooled availability, and
              bookings into your own scheduling tool — capabilities you won&apos;t find on message-taking AI or booking-link
              products.
            </p>
          </div>
          <FeatureGrid items={HOME_PILLARS} cols={3} />
          <p style={{ textAlign: "center", marginTop: "2rem" }}>
            <Link className="btn secondary" href="/features/">See the full platform →</Link>
          </p>
        </div>
      </section>

      <section className="section alt">
        <div className="wrap">
          <div className="split">
            <div>
              <div className="eyebrow">Team scheduling</div>
              <h2>One phone number. Your whole team&apos;s diary. Every day filled.</h2>
              <p className="lead" style={{ marginTop: "0.8rem" }}>
                Add each team member with their skills, areas and shifts. Callers hear the earliest slot anyone
                suitable is free for; ParlioTec assigns the job, books it into that team member&apos;s calendar with the full
                brief, and the office sees it all on the Schedule board.
              </p>
              <ul className="checks">
                <li>Booking rules: 60-minute slots on the hour or half-past, finish before close, 30 minutes travel between jobs</li>
                <li>Service types you create — “Boiler service, 90 min” — picked by the assistant from what the caller says</li>
                <li>Emergency exception: genuine emergencies can go out-of-hours or to the on-call team member</li>
                <li>Reassign, reschedule or cancel from the board; the calendar event moves with it</li>
              </ul>
            </div>
            <div className="panel-art">
              <div className="lanes">
                <div className="h" /><div className="h">Tom</div><div className="h">Priya</div><div className="h">Dan</div>
                <div className="t">08:00</div><div className="cell b1" /><div className="cell busy" /><div className="cell b3" />
                <div className="t">09:00</div><div className="cell" /><div className="cell b2" /><div className="cell b3" />
                <div className="t">10:00</div><div className="cell b1" /><div className="cell b2" /><div className="cell" />
                <div className="t">11:00</div><div className="cell b1" /><div className="cell" /><div className="cell b3" />
                <div className="t">12:00</div><div className="cell busy" /><div className="cell busy" /><div className="cell busy" />
                <div className="t">13:00</div><div className="cell b1" /><div className="cell b2" /><div className="cell b3" />
                <div className="t">14:00</div><div className="cell" /><div className="cell b2" /><div className="cell b3" />
                <div className="t">15:00</div><div className="cell b1" /><div className="cell" /><div className="cell" />
              </div>
              <p className="muted small" style={{ marginTop: "0.8rem" }}>Schedule board · day view · a colour per team member, hatched = busy from their own calendar</p>
            </div>
          </div>
        </div>
      </section>

      <section className="section analytics">
        <div className="wrap">
          <div className="section-head center">
            <div className="eyebrow">Analytics &amp; AI business advisor</div>
            <h2>Not just call logs. A view of your business you&apos;ve never had.</h2>
            <p className="lead">
              Every call, message and booking feeds analytics built for owners, not analysts: when demand really arrives,
              what callers want, where revenue is won and lost — with forecasts for next week and an AI advisor that
              tells you, in plain English, what to change and what it&apos;s worth.
            </p>
          </div>
          <Reveal className="shot-stage" from="up">
            <div className="shot main">
              <Image
                src="/shots/analytics-overview.webp"
                alt="ParlioTec analytics dashboard: total calls, answer rate, call volume trend, calls by day of week and Ask AI"
                width={1400}
                height={760}
                priority={false}
              />
            </div>
            <div className="shot-note a"><b>Ask AI</b>“after-hours calls this month vs last”</div>
            <div className="shot-note b"><b>Next 7 days</b>77 expected calls · +7%</div>
          </Reveal>
          <div className="grid c3 insight-grid">
            <Reveal className="feature" from="up" delay={0}>
              <div className="ico">📈</div>
              <h3>Trends you can act on</h3>
              <p>
                Hour-by-weekday heat-map of when callers ring, month and year comparisons, first-time vs returning, what
                callers asked for and what the assistant couldn&apos;t answer. Ask a question in English and get the chart.
              </p>
            </Reveal>
            <Reveal className="feature" from="up" delay={90}>
              <div className="ico">🔮</div>
              <h3>Forecasts and staffing</h3>
              <p>
                Expected calls per day for the week ahead with a likely range, peak concurrency, busiest and quietest
                hours, calls that would have been missed, and utilisation per team member — so you staff to demand.
              </p>
            </Reveal>
            <Reveal className="feature" from="up" delay={180}>
              <div className="ico">🧠</div>
              <h3>Recommendations with the evidence</h3>
              <p>
                The AI advisor reads your own numbers and proposes specific changes — plan cover for next week&apos;s
                peak, cover the after-hours calls you&apos;re losing, fix the department that misses its transfers, answer the
                question callers keep asking — each with the data behind it, the expected impact and a one-click
                apply, then measures whether it worked.
              </p>
            </Reveal>
          </div>
          <div className="split shots-2">
            <Reveal className="shot" from="left">
              <Image
                src="/shots/analytics-insights.webp"
                alt="Insights: next 7 days forecast, calls that would have been missed, peak concurrency and a when-callers-ring heat-map"
                width={1400}
                height={820}
              />
              <p className="muted small">Demand: forecast, missed-call exposure and the weekday × hour heat-map</p>
            </Reveal>
            <Reveal className="shot" from="right">
              <Image
                src="/shots/analytics-advisor.webp"
                alt="AI advisor recommendation card with evidence, expected impact and Apply button"
                width={1400}
                height={820}
              />
              <p className="muted small">Advisor: evidence-backed recommendations, applied in one click and measured afterwards</p>
            </Reveal>
          </div>
          <p style={{ textAlign: "center", marginTop: "2rem" }}>
            <Link className="btn secondary" href="/features/#measure">Everything in Measure &amp; improve →</Link>
          </p>
        </div>
      </section>

      <section className="section dark improve">
        <div className="wrap">
          <div className="split">
            <div>
              <div className="eyebrow" style={{ color: "#c7b9ff" }}>Self-improving AI</div>
              <h2>The more it hears, the better it gets.</h2>
              <p className="lead" style={{ marginTop: "0.8rem" }}>
                Most phone AI is as good on day 300 as on day one. ParlioTec scores every call, spots the questions it
                couldn&apos;t answer and the moments callers hesitated, and drafts the fix — new FAQ wording, a clearer
                rule, a better service type — then proves it against real conversations before you approve it.
              </p>
              <ul className="checks">
                <li>QA scores every call; low scores raise an alert with the transcript moment</li>
                <li>Unanswered questions are clustered into suggested FAQs and rules</li>
                <li>Drafts are replayed against your real scenarios in the sandbox — only genuine improvements are proposed</li>
                <li>A regression pack of past calls gates every publish, so it never gets worse</li>
                <li>You approve each change; the advisor then measures the effect in next week&apos;s numbers</li>
              </ul>
            </div>
            <Reveal className="loop-art" from="right">
              <div className="loop">
                <div className="node"><b>Calls, chats &amp; bookings</b><span>every conversation, every channel</span></div>
                <div className="arrow">→</div>
                <div className="node"><b>QA &amp; insight engine</b><span>scores, gaps, hesitation, intents</span></div>
                <div className="arrow">→</div>
                <div className="node"><b>Proposed improvements</b><span>FAQs, rules, service types, wording</span></div>
                <div className="arrow">→</div>
                <div className="node"><b>Tested against real scenarios</b><span>sandbox + regression pack</span></div>
                <div className="arrow">→</div>
                <div className="node approve"><b>You approve · it measures</b><span>impact shown in next week&apos;s report</span></div>
              </div>
            </Reveal>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="wrap">
          <div className="section-head center">
            <div className="eyebrow">How it works</div>
            <h2>Live in an afternoon, better every week</h2>
          </div>
          <Steps />
        </div>
      </section>

      <section className="section alt" id="demo">
        <div className="wrap">
          <HearItLive />
        </div>
      </section>

      <section className="section">
        <div className="wrap">
          <div className="section-head center">
            <div className="eyebrow">ParlioTec vs the alternatives</div>
            <h2>Compare it honestly</h2>
            <p className="lead">The things a business actually needs from the phone — and which kind of product does them.</p>
          </div>
          <CompareTable rows={HOME_COMPARE} />
          <p style={{ textAlign: "center", marginTop: "1.6rem" }}>
            <Link className="btn secondary" href="/compare/">Full comparison →</Link>
          </p>
        </div>
      </section>

      <section className="section alt">
        <div className="wrap">
          <div className="section-head center">
            <div className="eyebrow">Who it&apos;s for</div>
            <h2>Built for businesses whose phone is their front door</h2>
          </div>
          <Industries />
        </div>
      </section>

      <section className="section dark">
        <div className="wrap">
          <div className="section-head center">
            <div className="eyebrow" style={{ color: "#c7b9ff" }}>Trust</div>
            <h2>UK-first by design</h2>
            <p className="lead">Your callers&apos; data is handled in the UK, with the controls a regulated business expects.</p>
          </div>
          <div className="grid c4">
            <div className="feature"><div className="ico">🇬🇧</div><h3>UK hosting</h3><p>Voice, API and recordings run in UK regions; UK-sovereign deployment available on Enterprise.</p></div>
            <div className="feature"><div className="ico">🎙</div><h3>Consent built in</h3><p>Recording announcements, per-tenant consent wording and a call-recording notice for your customers.</p></div>
            <div className="feature"><div className="ico">🧹</div><h3>Redaction & retention</h3><p>PII redaction, retention policies per data type, GDPR export and erasure on request.</p></div>
            <div className="feature"><div className="ico">🔐</div><h3>Access control</h3><p>Roles, 2FA, session revocation, audit log of every change, human approvals for sensitive actions.</p></div>
          </div>
          <p style={{ textAlign: "center", marginTop: "2rem" }}>
            <Link className="btn light" href="/security/">Trust &amp; security overview</Link>
          </p>
        </div>
      </section>

      <CtaBand />
    </>
  );
}
