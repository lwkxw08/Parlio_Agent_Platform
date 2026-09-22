import type { MetadataRoute } from "next";
import { SITE_URL } from "@/lib/api";

const ROUTES: [string, MetadataRoute.Sitemap[number]["changeFrequency"], number][] = [
  ["/", "weekly", 1],
  ["/features/", "monthly", 0.9],
  ["/pricing/", "monthly", 0.9],
  ["/industries/", "monthly", 0.8],
  ["/how-it-works/", "monthly", 0.8],
  ["/compare/", "monthly", 0.7],
  ["/demo/", "monthly", 0.7],
  ["/contact/", "yearly", 0.6],
  ["/security/", "yearly", 0.5],
  ["/legal/terms/", "yearly", 0.2],
  ["/legal/privacy/", "yearly", 0.2],
  ["/legal/cookies/", "yearly", 0.1],
  ["/legal/acceptable-use/", "yearly", 0.1],
  ["/legal/dpa/", "yearly", 0.1],
  ["/legal/call-recording/", "yearly", 0.1],
];

export const dynamic = "force-static";

export default function sitemap(): MetadataRoute.Sitemap {
  const lastModified = new Date();
  return ROUTES.map(([path, changeFrequency, priority]) => ({ url: `${SITE_URL}${path}`, lastModified, changeFrequency, priority }));
}
