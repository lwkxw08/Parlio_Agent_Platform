"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

type Item = { href: string; label: string; icon: React.ReactNode };

const S = { fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round", strokeLinejoin: "round" } as const;
const I = {
  home: <svg viewBox="0 0 24 24" {...S}><path d="M3 11l9-8 9 8v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z" /></svg>,
  calls: <svg viewBox="0 0 24 24" {...S}><path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1.9.4 1.8.7 2.6a2 2 0 0 1-.5 2.1L8 9.7a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.8.3 1.7.5 2.6.7a2 2 0 0 1 1.7 2z" /></svg>,
  live: <svg viewBox="0 0 24 24" {...S}><circle cx="12" cy="12" r="2.5" /><path d="M8.5 8.5a5 5 0 0 0 0 7M15.5 8.5a5 5 0 0 1 0 7" /><path d="M5.6 5.6a9 9 0 0 0 0 12.8M18.4 5.6a9 9 0 0 1 0 12.8" /></svg>,
  analytics: <svg viewBox="0 0 24 24" {...S}><path d="M3 3v18h18" /><path d="M7 15l4-5 4 3 5-7" /></svg>,
  contacts: <svg viewBox="0 0 24 24" {...S}><circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" /></svg>,
  tickets: <svg viewBox="0 0 24 24" {...S}><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" /></svg>,
  outbound: <svg viewBox="0 0 24 24" {...S}><path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1.9.4 1.8.7 2.7a2 2 0 0 1-.5 2.1L8 9.8a16 16 0 0 0 6.2 6.2l1.3-1.3a2 2 0 0 1 2.1-.5c.9.3 1.8.6 2.7.7a2 2 0 0 1 1.7 2z" /><path d="M15 3h6v6" /><path d="M21 3l-7 7" /></svg>,
  transfers: <svg viewBox="0 0 24 24" {...S}><path d="M17 3l4 4-4 4" /><path d="M3 7h18" /><path d="M7 21l-4-4 4-4" /><path d="M21 17H3" /></svg>,
  assistant: <svg viewBox="0 0 24 24" {...S}><rect x="4" y="8" width="16" height="12" rx="3" /><path d="M12 8V4" /><circle cx="9" cy="14" r="1" /><circle cx="15" cy="14" r="1" /></svg>,
  integrations: <svg viewBox="0 0 24 24" {...S}><path d="M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1" /><path d="M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1" /></svg>,
  telephony: <svg viewBox="0 0 24 24" {...S}><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M7 9h2M11 9h2M15 9h2M7 13h2M11 13h2M15 13h2M7 17h10" /></svg>,
  billing: <svg viewBox="0 0 24 24" {...S}><rect x="2" y="5" width="20" height="14" rx="2" /><path d="M2 10h20" /></svg>,
  compliance: <svg viewBox="0 0 24 24" {...S}><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" /><path d="M9 12l2 2 4-4" /></svg>,
  team: <svg viewBox="0 0 24 24" {...S}><circle cx="9" cy="8" r="3.5" /><path d="M2 20a7 7 0 0 1 14 0" /><circle cx="17" cy="9" r="2.5" /><path d="M16 15a5 5 0 0 1 6 5" /></svg>,
  launch: <svg viewBox="0 0 24 24" {...S}><path d="M5 15l-2 6 6-2" /><path d="M14 4c3 0 6 3 6 6-2 6-8 10-11 10L4 15C4 12 8 6 14 4z" /><circle cx="15" cy="9" r="1.5" /></svg>,
  account: <svg viewBox="0 0 24 24" {...S}><circle cx="12" cy="12" r="10" /><circle cx="12" cy="10" r="3.2" /><path d="M6 19a6.5 6.5 0 0 1 12 0" /></svg>,
  sun: <svg viewBox="0 0 24 24" {...S}><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></svg>,
  moon: <svg viewBox="0 0 24 24" {...S}><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" /></svg>,
};

export const NAV: Item[] = [
  { href: "/", label: "Overview", icon: I.home },
  { href: "/calls", label: "Calls", icon: I.calls },
  { href: "/live", label: "Live", icon: I.live },
  { href: "/analytics", label: "Analytics", icon: I.analytics },
  { href: "/contacts", label: "Contacts", icon: I.contacts },
  { href: "/tickets", label: "Tickets", icon: I.tickets },
  { href: "/handoff", label: "Transfers", icon: I.transfers },
  { href: "/outbound", label: "Outbound", icon: I.outbound },
  { href: "/assistant", label: "Assistant", icon: I.assistant },
  { href: "/integrations", label: "Integrations", icon: I.integrations },
  { href: "/telephony", label: "Telephony", icon: I.telephony },
  { href: "/billing", label: "Billing", icon: I.billing },
  { href: "/compliance", label: "Compliance", icon: I.compliance },
  { href: "/team", label: "Team", icon: I.team },
  { href: "/launch", label: "Launch", icon: I.launch },
];

export type Account =
  | { kind: "user"; name: string; email: string; dev: boolean }
  | { kind: "signin" }
  | { kind: "offline" };

type Theme = "light" | "dark";
const THEME_KEY = "parlio-theme";

function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>("light");
  useEffect(() => {
    setTheme(document.documentElement.dataset.theme === "dark" ? "dark" : "light");
  }, []);
  const toggle = () => {
    const next: Theme = theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem(THEME_KEY, next);
    setTheme(next);
  };
  return (
    <button type="button" className="nav-item" onClick={toggle} title={theme === "dark" ? "Switch to day mode" : "Switch to night mode"}>
      {theme === "dark" ? I.sun : I.moon}
      <span>{theme === "dark" ? "Day mode" : "Night mode"}</span>
    </button>
  );
}

export default function Sidebar({ account }: { account: Account }) {
  const path = usePathname();
  const isActive = (href: string) => (href === "/" ? path === "/" : path.startsWith(href));
  return (
    <aside className="side">
      <Link href="/" className="logo" aria-label="Parlio home">
        <Image src="/logo-icon.png" alt="Parlio" width={40} height={40} priority />
      </Link>
      <nav>
        {NAV.map((it) => (
          <Link key={it.href} href={it.href} className={`nav-item${isActive(it.href) ? " active" : ""}`} title={it.label}>
            {it.icon}
            <span>{it.label}</span>
          </Link>
        ))}
      </nav>
      <div className="side-foot">
        <ThemeToggle />
        {account.kind === "user" ? (
          <Link href="/account" className={`nav-item${isActive("/account") ? " active" : ""}`} title={account.email}>
            {I.account}
            <span>
              {account.name}
              {account.dev && <span className="pill warn" style={{ marginLeft: 6 }}>dev</span>}
            </span>
          </Link>
        ) : account.kind === "signin" ? (
          <Link href="/login" className="nav-item" title="Sign in">
            {I.account}
            <span>Sign in</span>
          </Link>
        ) : (
          <div className="nav-item muted" title="API offline">
            {I.account}
            <span className="small">API offline</span>
          </div>
        )}
      </div>
    </aside>
  );
}
