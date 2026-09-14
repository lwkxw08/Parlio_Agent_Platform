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
  inbox: <svg viewBox="0 0 24 24" {...S}><path d="M22 12h-6l-2 3h-4l-2-3H2" /><path d="M5.5 5.1L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.5-6.9A2 2 0 0 0 16.7 4H7.3a2 2 0 0 0-1.8 1.1z" /></svg>,
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
  quality: <svg viewBox="0 0 24 24" {...S}><path d="M12 2l2.9 6.3 6.9.8-5.1 4.7 1.4 6.8L12 17.3 5.9 20.6l1.4-6.8L2.2 9.1l6.9-.8z" /></svg>,
  value: <svg viewBox="0 0 24 24" {...S}><circle cx="12" cy="12" r="9" /><path d="M14.5 9.2c-.4-.9-1.4-1.4-2.5-1.4-1.6 0-2.7.9-2.7 2 0 2.6 5.6 1.3 5.6 4.1 0 1.2-1.2 2.1-2.9 2.1-1.3 0-2.4-.6-2.8-1.6" /><path d="M12 6v2M12 16v2" /></svg>,
  settings: <svg viewBox="0 0 24 24" {...S}><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" /></svg>,
  compliance: <svg viewBox="0 0 24 24" {...S}><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" /><path d="M9 12l2 2 4-4" /></svg>,
  health: <svg viewBox="0 0 24 24" {...S}><path d="M3 12h4l2-5 4 10 2-5h6" /></svg>,
  support: <svg viewBox="0 0 24 24" {...S}><circle cx="12" cy="12" r="9" /><path d="M9.5 9.5a2.5 2.5 0 0 1 5 0c0 1.7-2.5 2-2.5 3.5" /><path d="M12 17h.01" /></svg>,
  team: <svg viewBox="0 0 24 24" {...S}><circle cx="9" cy="8" r="3.5" /><path d="M2 20a7 7 0 0 1 14 0" /><circle cx="17" cy="9" r="2.5" /><path d="M16 15a5 5 0 0 1 6 5" /></svg>,
  setup: <svg viewBox="0 0 24 24" {...S}><rect x="4" y="3" width="16" height="18" rx="2" /><path d="M8 8h8M8 12h8M8 16h5" /></svg>,
  launch: <svg viewBox="0 0 24 24" {...S}><path d="M5 15l-2 6 6-2" /><path d="M14 4c3 0 6 3 6 6-2 6-8 10-11 10L4 15C4 12 8 6 14 4z" /><circle cx="15" cy="9" r="1.5" /></svg>,
  account: <svg viewBox="0 0 24 24" {...S}><circle cx="12" cy="12" r="10" /><circle cx="12" cy="10" r="3.2" /><path d="M6 19a6.5 6.5 0 0 1 12 0" /></svg>,
  sun: <svg viewBox="0 0 24 24" {...S}><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></svg>,
  moon: <svg viewBox="0 0 24 24" {...S}><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" /></svg>,
  admin: <svg viewBox="0 0 24 24" {...S}><path d="M12 2l8 3v6c0 5-3.5 9.4-8 11-4.5-1.6-8-6-8-11V5z" /><path d="M12 8v4M12 15h.01" /></svg>,
};

export const NAV: Item[] = [
  { href: "/", label: "Overview", icon: I.home },
  { href: "/setup", label: "Setup", icon: I.setup },
];

export type Group = { key: string; label: string; icon: React.ReactNode; items: Item[] };

export const GROUPS: Group[] = [
  {
    key: "conversations",
    label: "Conversations",
    icon: I.calls,
    items: [
      { href: "/calls", label: "Calls", icon: I.calls },
      { href: "/live", label: "Live", icon: I.live },
      { href: "/inbox", label: "Inbox", icon: I.inbox },
      { href: "/tickets", label: "Tickets", icon: I.tickets },
      { href: "/handoff", label: "Transfers", icon: I.transfers },
      { href: "/outbound", label: "Outbound", icon: I.outbound },
      { href: "/contacts", label: "Contacts", icon: I.contacts },
    ],
  },
  {
    key: "assistant",
    label: "Assistant",
    icon: I.assistant,
    items: [
      { href: "/assistant", label: "Assistant Studio", icon: I.assistant },
      { href: "/quality", label: "Quality & simulate", icon: I.quality },
      { href: "/integrations", label: "Integrations", icon: I.integrations },
      { href: "/telephony", label: "Telephony", icon: I.telephony },
      { href: "/launch", label: "Launch guide", icon: I.launch },
    ],
  },
  {
    key: "insights",
    label: "Insights",
    icon: I.analytics,
    items: [
      { href: "/analytics", label: "Analytics", icon: I.analytics },
      { href: "/value", label: "Value", icon: I.value },
      { href: "/health", label: "Health", icon: I.health },
    ],
  },
  {
    key: "account",
    label: "Account",
    icon: I.contacts,
    items: [
      { href: "/billing", label: "Billing", icon: I.billing },
      { href: "/compliance", label: "Compliance", icon: I.compliance },
      { href: "/support", label: "Support", icon: I.support },
      { href: "/settings", label: "Settings", icon: I.settings },
      { href: "/team", label: "Team", icon: I.team },
    ],
  },
];

const OPEN_KEY = "parlio-nav-open";

export type Account =
  | { kind: "user"; name: string; email: string; dev: boolean; staff: boolean; viewAs: string | null }
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

function loadOpen(): Record<string, boolean> {
  try {
    const raw = localStorage.getItem(OPEN_KEY);
    return raw ? (JSON.parse(raw) as Record<string, boolean>) : {};
  } catch {
    return {};
  }
}

export default function Sidebar({ account }: { account: Account }) {
  const path = usePathname();
  const isActive = (href: string) => (href === "/" ? path === "/" : path.startsWith(href));
  const activeGroup = GROUPS.find((g) => g.items.some((it) => isActive(it.href)))?.key ?? null;
  const [open, setOpen] = useState<Record<string, boolean>>({});
  useEffect(() => setOpen(loadOpen()), []);
  useEffect(() => {
    if (activeGroup) setOpen((o) => (o[activeGroup] ? o : { ...o, [activeGroup]: true }));
  }, [activeGroup]);
  const toggle = (key: string) =>
    setOpen((o) => {
      const next = { ...o, [key]: !o[key] };
      localStorage.setItem(OPEN_KEY, JSON.stringify(next));
      return next;
    });
  if (path.startsWith("/chat/")) return null;
  const staff = account.kind === "user" && account.staff;
  const link = (it: Item) => (
    <Link key={it.href} href={it.href} className={`nav-item${isActive(it.href) ? " active" : ""}`} title={it.label}>
      {it.icon}
      <span>{it.label}</span>
    </Link>
  );
  return (
    <aside className="side">
      <Link href="/" className="logo" aria-label="Parlio home">
        <Image src="/logo-icon.png" alt="Parlio" width={40} height={40} priority />
      </Link>
      <nav>
        {NAV.map(link)}
        {GROUPS.map((g) => {
          const isOpen = !!open[g.key];
          const current = activeGroup === g.key;
          return (
            <div key={g.key} className={`nav-group${isOpen ? " open" : ""}${current ? " current" : ""}`}>
              <button type="button" className={`nav-item nav-group-head${current && !isOpen ? " active" : ""}`} onClick={() => toggle(g.key)} aria-expanded={isOpen} title={g.label}>
                {g.icon}
                <span>{g.label}</span>
                <svg className="chev" viewBox="0 0 24 24" {...S} aria-hidden><path d="M9 6l6 6-6 6" /></svg>
              </button>
              <div className="nav-children">{g.items.map(link)}</div>
            </div>
          );
        })}
        {staff && (
          <Link href="/admin" className={`nav-item${isActive("/admin") ? " active" : ""}`} title="Platform admin">
            {I.admin}
            <span>Platform admin</span>
          </Link>
        )}
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
