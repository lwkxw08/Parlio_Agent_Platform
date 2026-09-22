import type { Metadata } from "next";
import { SITE_URL } from "@/lib/api";

export const SITE_NAME = "ParlioTec";
export const ORG_NAME = "KMDR Holdings Ltd";
export const OG_IMAGE = "/brand/og-card.png";

/** Canonical URL + per-page title/description/OG for a static route (trailing slash to match the export). */
export function pageMeta(path: string, title: string, description: string): Metadata {
  return {
    title,
    description,
    alternates: { canonical: path },
    openGraph: { title: `${title} · ${SITE_NAME}`, description, url: path, images: [{ url: OG_IMAGE, width: 1200, height: 630 }] },
    twitter: { card: "summary_large_image", title: `${title} · ${SITE_NAME}`, description, images: [OG_IMAGE] },
  };
}

export const abs = (path: string) => `${SITE_URL}${path}`;

export const organizationLd = {
  "@context": "https://schema.org",
  "@type": "Organization",
  name: SITE_NAME,
  legalName: ORG_NAME,
  url: SITE_URL,
  logo: abs("/brand/icon-512.png"),
  description: "Intelligent AI-powered business phone system for UK businesses: answers every call, books appointments across the team and transfers warm to a human.",
  address: { "@type": "PostalAddress", streetAddress: "124-128 City Road", addressLocality: "London", postalCode: "EC1V 2NX", addressCountry: "GB" },
  contactPoint: [
    { "@type": "ContactPoint", contactType: "sales", email: "hello@parliotec.com", areaServed: "GB", availableLanguage: "en-GB" },
    { "@type": "ContactPoint", contactType: "customer support", url: abs("/contact/"), areaServed: "GB", availableLanguage: "en-GB" },
  ],
};

export const websiteLd = {
  "@context": "https://schema.org",
  "@type": "WebSite",
  name: SITE_NAME,
  url: SITE_URL,
  inLanguage: "en-GB",
};

export function softwareLd(offers: { name: string; monthly_pence: number }[]) {
  return {
    "@context": "https://schema.org",
    "@type": "SoftwareApplication",
    name: SITE_NAME,
    applicationCategory: "BusinessApplication",
    operatingSystem: "Web",
    url: SITE_URL,
    description: "AI phone answering and appointment booking for UK businesses: natural-voice assistant, rules-based booking into your team's calendars, warm transfers, SMS/WhatsApp/web chat, analytics and AI recommendations.",
    ...(offers.length === 0 ? {} : { offers: offers.map((o) => ({
      "@type": "Offer",
      name: o.name,
      price: (o.monthly_pence / 100).toFixed(2),
      priceCurrency: "GBP",
      url: abs("/pricing/"),
      priceSpecification: { "@type": "UnitPriceSpecification", price: (o.monthly_pence / 100).toFixed(2), priceCurrency: "GBP", billingDuration: "P1M" },
    })) }),
  };
}

export function faqLd(items: readonly (readonly [string, string])[]) {
  return {
    "@context": "https://schema.org",
    "@type": "FAQPage",
    mainEntity: items.map(([q, a]) => ({ "@type": "Question", name: q, acceptedAnswer: { "@type": "Answer", text: a } })),
  };
}

export function breadcrumbLd(trail: [string, string][]) {
  return {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    itemListElement: trail.map(([name, path], i) => ({ "@type": "ListItem", position: i + 1, name, item: abs(path) })),
  };
}
