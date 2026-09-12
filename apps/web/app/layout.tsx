import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Parlio",
  description: "Parlio AI phone assistant dashboard",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header>
          <Link href="/" className="brand">Parlio</Link>
          <nav>
            <Link href="/">Overview</Link>
            <Link href="/calls">Calls</Link>
            <Link href="/tickets">Tickets</Link>
            <Link href="/handoff">Transfers</Link>
          </nav>
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}
