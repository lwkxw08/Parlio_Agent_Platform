import type { Metadata } from "next";
import type { ReactNode } from "react";
import { Footer } from "@/components/footer";
import { Header } from "@/components/header";
import { JsonLd } from "@/components/json-ld";
import { API_URL, SITE_URL } from "@/lib/api";
import { OG_IMAGE, organizationLd, websiteLd } from "@/lib/seo";
import "./globals.css";

const HOME_TITLE = "Stop losing revenue to missed calls | ParlioTec AI business phone system";
const HOME_DESCRIPTION =
  "Every missed call is revenue that goes to a competitor. ParlioTec's AI-powered business phone system answers every call in a natural voice, books appointments straight into your team's calendars, transfers warm to a human and keeps your data in the UK.";

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: { default: HOME_TITLE, template: "%s · ParlioTec" },
  description: HOME_DESCRIPTION,
  applicationName: "ParlioTec",
  keywords: [
    "AI business phone system",
    "missed calls",
    "AI phone answering UK",
    "appointment booking by phone",
    "call answering for small business",
    "AI call handling",
  ],
  alternates: { canonical: "/" },
  robots: { index: true, follow: true, googleBot: { index: true, follow: true, "max-image-preview": "large", "max-snippet": -1 } },
  icons: { icon: "/brand/icon-32.png", apple: "/brand/icon-180.png" },
  openGraph: {
    type: "website",
    locale: "en_GB",
    siteName: "ParlioTec",
    url: "/",
    title: HOME_TITLE,
    description: HOME_DESCRIPTION,
    images: [{ url: OG_IMAGE, width: 1200, height: 630, alt: "ParlioTec — every call you miss, someone else answers" }],
  },
  twitter: { card: "summary_large_image", title: HOME_TITLE, description: HOME_DESCRIPTION, images: [OG_IMAGE] },
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en-GB">
      <head>
        <link rel="preconnect" href={API_URL} crossOrigin="anonymous" />
        <JsonLd data={[organizationLd, websiteLd]} />
      </head>
      <body>
        <Header />
        <main>{children}</main>
        <Footer />
      </body>
    </html>
  );
}
