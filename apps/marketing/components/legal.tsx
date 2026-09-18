import Link from "next/link";
import type { ReactNode } from "react";

export const LEGAL_LINKS = [
  ["/legal/terms/", "Terms of service"],
  ["/legal/privacy/", "Privacy policy"],
  ["/legal/cookies/", "Cookie policy"],
  ["/legal/acceptable-use/", "Acceptable use policy"],
  ["/legal/dpa/", "Data processing agreement"],
  ["/legal/call-recording/", "Call recording notice"],
] as const;

/** Bracketed value the business must fill in before launch. */
export function P({ children }: { children: ReactNode }) {
  return <span className="placeholder">[{children}]</span>;
}

export function LegalPage({ title, updated, children, current }: { title: string; updated: string; children: ReactNode; current: string }) {
  return (
    <>
      <section className="page-hero" style={{ paddingBottom: "1.5rem" }}>
        <div className="wrap">
          <div className="eyebrow">Legal</div>
          <h1>{title}</h1>
          <p className="muted">Last updated: {updated}</p>
        </div>
      </section>
      <section className="section" style={{ paddingTop: "1rem" }}>
        <div className="wrap">
          <nav aria-label="Legal documents" className="legal-nav">
            {LEGAL_LINKS.map(([href, label]) => (
              <Link key={href} href={href} className={href === current ? "active" : ""}>{label}</Link>
            ))}
          </nav>
          <article className="legal">
            <div className="notice">
              <b>Draft for review.</b> This document has been prepared as a launch-ready draft and must be reviewed by a
              qualified UK solicitor before publication. Items shown <span className="placeholder">[like this]</span> must
              be completed with the company&apos;s details.
            </div>
            {children}
          </article>
        </div>
      </section>
    </>
  );
}
