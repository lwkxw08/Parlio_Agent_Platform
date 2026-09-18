import type { Metadata } from "next";
import type { ReactNode } from "react";
import { Footer } from "@/components/footer";
import { Header } from "@/components/header";
import { SITE_URL } from "@/lib/api";
import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: {
    default: "ParlioTec — every call you miss, someone else answers. The intelligent AI-powered business phone system",
    template: "%s · ParlioTec",
  },
  description:
    "UK-first intelligent AI-powered business phone system that stops missed calls costing you work: answers every call in a natural voice, books real appointments across your team, transfers warm to a human, handles SMS, WhatsApp and web chat, and turns every conversation into analytics, forecasts and AI recommendations.",
  icons: { icon: "/brand/icon-32.png", apple: "/brand/icon-180.png" },
  openGraph: {
    type: "website",
    siteName: "ParlioTec",
    title: "ParlioTec — intelligent AI-powered business phone system for UK businesses",
    description:
      "Answers every call, books real appointments across your team, transfers warm to a human and keeps data in the UK.",
    images: [{ url: "/brand/full-logo.png", width: 1280, height: 1024 }],
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
