import type { Metadata } from "next";
import type { ReactNode } from "react";
import { Footer } from "@/components/footer";
import { Header } from "@/components/header";
import { SITE_URL } from "@/lib/api";
import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: {
    default: "ParlioTec — the AI phone assistant that books the job, not just the message",
    template: "%s · ParlioTec",
  },
  description:
    "UK-first AI receptionist for businesses: answers every call in a natural voice, books real appointments across your team's calendars or scheduling tool, transfers warm to a human, and handles SMS, WhatsApp and web chat too.",
  icons: { icon: "/brand/icon-32.png", apple: "/brand/icon-180.png" },
  openGraph: {
    type: "website",
    siteName: "ParlioTec",
    title: "ParlioTec — AI phone assistant for UK businesses",
    description:
      "Answers every call, books real appointments across your team, transfers warm to a human and keeps data in the UK.",
    images: [{ url: "/brand/full-logo.jpg", width: 1280, height: 746 }],
  },
  twitter: { card: "summary_large_image" },
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en-GB">
      <body>
        <Header />
        <main>{children}</main>
        <Footer />
      </body>
    </html>
  );
}
