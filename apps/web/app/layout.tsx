import type { Metadata } from "next";
import Link from "next/link";
import { fetchMe } from "@/lib/api";
import "./globals.css";

export const metadata: Metadata = {
  title: "Parlio",
  description: "Parlio AI phone assistant dashboard",
};

export const dynamic = "force-dynamic";

const NAV = [
  ["/", "Overview"],
  ["/calls", "Calls"],
  ["/analytics", "Analytics"],
  ["/contacts", "Contacts"],
  ["/tickets", "Tickets"],
  ["/handoff", "Transfers"],
  ["/assistant", "Assistant"],
  ["/integrations", "Integrations"],
  ["/telephony", "Telephony"],
  ["/team", "Team"],
  ["/launch", "Launch"],
] as const;

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const me = await fetchMe();
  return (
    <html lang="en">
      <body>
        <header>
          <Link href="/" className="brand">Parlio</Link>
          <nav>
            {NAV.map(([href, label]) => <Link key={href} href={href}>{label}</Link>)}
          </nav>
          <div className="account">
            {me.ok ? (
              <Link href="/account" title={me.data.email}>
                {me.data.name ?? me.data.email}
                {me.data.mode === "dev" && <span className="pill warn" style={{ marginLeft: 6 }}>dev</span>}
              </Link>
            ) : me.status === 401 ? (
              <Link href="/login">Sign in</Link>
            ) : (
              <span className="muted small">API offline</span>
            )}
          </div>
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}
