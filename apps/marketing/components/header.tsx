"use client";

import Image from "next/image";
import Link from "next/link";
import { useState } from "react";
import { loginUrl, signupUrl } from "@/lib/api";

const NAV = [
  { href: "/features/", label: "Platform" },
  { href: "/compare/", label: "Why ParlioTec" },
  { href: "/industries/", label: "Industries" },
  { href: "/pricing/", label: "Pricing" },
  { href: "/demo/", label: "Hear it live" },
  { href: "/security/", label: "Trust" },
];

export function Header() {
  const [open, setOpen] = useState(false);
  return (
    <header className="site-header">
      <div className="wrap">
        <Link href="/" className="brand" aria-label="ParlioTec home">
          <Image src="/brand/icon.png" alt="" width={52} height={38} className="icon" priority />
          <Image src="/brand/wordmark.png" alt="ParlioTec" width={156} height={20} className="word" priority />
        </Link>
        <nav className="nav" aria-label="Main">
          {NAV.map((n) => (
            <Link key={n.href} href={n.href}>{n.label}</Link>
          ))}
        </nav>
        <div className="nav-cta">
          <a className="btn ghost sm" href={loginUrl}>Sign in</a>
          <a className="btn primary sm" href={signupUrl}>Start free trial</a>
        </div>
        <button type="button" className="menu-btn" aria-label="Menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
          <span /><span /><span />
        </button>
      </div>
      {open && (
        <div className="wrap mobile-nav">
          {NAV.map((n) => (
            <Link key={n.href} href={n.href} onClick={() => setOpen(false)}>{n.label}</Link>
          ))}
          <Link href="/contact/" onClick={() => setOpen(false)}>Contact</Link>
          <a className="btn secondary" href={loginUrl}>Sign in</a>
          <a className="btn primary" href={signupUrl}>Start free trial</a>
        </div>
      )}
    </header>
  );
}
