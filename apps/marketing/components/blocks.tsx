import Link from "next/link";
import { COMPARE, type Feature, INDUSTRIES, STEPS } from "@/lib/content";
import { signupUrl } from "@/lib/api";

export function FeatureGrid({ items, cols = 3 }: { items: Feature[]; cols?: 2 | 3 | 4 }) {
  return (
    <div className={`grid c${cols}`}>
      {items.map((f) => (
        <article key={f.title} className="feature">
          <div className="ico" aria-hidden>{f.icon}</div>
          {f.tag && <span className="tag">{f.tag}</span>}
          <h3>{f.title}</h3>
          <p>{f.body}</p>
          {f.points && (
            <ul>
              {f.points.map((p) => <li key={p}>{p}</li>)}
            </ul>
          )}
        </article>
      ))}
    </div>
  );
}

function Mark({ v }: { v: string }) {
  if (v === "yes") return <span className="yes" aria-label="Yes">✓</span>;
  if (v === "no") return <span className="no" aria-label="No">—</span>;
  if (v === "part") return <span className="part" aria-label="Partial">◐</span>;
  return <span className="muted small">{v}</span>;
}

export function CompareTable({ rows = COMPARE }: { rows?: typeof COMPARE }) {
  return (
    <>
      <div className="compare-scroll">
        <table className="compare">
          <thead>
            <tr>
              <th>Capability</th>
              <th className="us">ParlioTec</th>
              <th>Answering service</th>
              <th>Phone system / IVR</th>
              <th>Chatbot-only AI</th>
              <th>Booking-link tools</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.label}>
                <td>{r.label}</td>
                <td className="us"><Mark v={r.us} /></td>
                <td><Mark v={r.answering} /></td>
                <td><Mark v={r.ivr} /></td>
                <td><Mark v={r.chatbot} /></td>
                <td><Mark v={r.booking} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="compare-note">
        ✓ included · ◐ partial or add-on · — not offered. Categories describe typical products in each class, not any
        named vendor; check individual providers for their current features.
      </p>
    </>
  );
}

export function Steps() {
  return (
    <div className="steps">
      {STEPS.map((s, i) => (
        <div key={s.title} className="step">
          <div className="n">{i + 1}</div>
          <h3>{s.title}</h3>
          <p>{s.body}</p>
        </div>
      ))}
    </div>
  );
}

export function Industries({ full = false }: { full?: boolean }) {
  return (
    <div className="grid c3">
      {INDUSTRIES.map((i) => (
        <article key={i.id} id={i.id} className="industry">
          <h3>{i.title}</h3>
          <p>{i.body}</p>
          {full && (
            <ul className="small muted" style={{ marginTop: "0.7rem", paddingLeft: "1.1rem" }}>
              {i.pains.map((p) => <li key={p}>Solves: {p}</li>)}
            </ul>
          )}
        </article>
      ))}
    </div>
  );
}

export function CtaBand({ title, body }: { title?: string; body?: string }) {
  return (
    <section className="section">
      <div className="wrap">
        <div className="cta-band">
          <div>
            <h2>{title ?? "Stop losing the calls you paid to get."}</h2>
            <p>{body ?? "Set up in an afternoon. Free trial on every plan, no card needed. Keep your number, your calendar and your team's way of working."}</p>
          </div>
          <div className="actions">
            <a className="btn primary lg" href={signupUrl}>Start free trial</a>
            <Link className="btn light lg" href="/demo/">Hear it live</Link>
          </div>
        </div>
      </div>
    </section>
  );
}

export function PageHero({ eyebrow, title, lead, children }: { eyebrow: string; title: string; lead: string; children?: React.ReactNode }) {
  return (
    <section className="page-hero">
      <div className="wrap">
        <div className="eyebrow">{eyebrow}</div>
        <h1>{title}</h1>
        <p className="lead">{lead}</p>
        {children}
      </div>
    </section>
  );
}
