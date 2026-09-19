import { fetchAdminAnalytics } from "@/lib/api";
import Analytics from "./analytics";

export const dynamic = "force-dynamic";

export default async function Page({ searchParams }: { searchParams: Promise<{ days?: string }> }) {
  const sp = await searchParams;
  const days = [7, 30, 90].includes(Number(sp.days)) ? Number(sp.days) : 30;
  const a = await fetchAdminAnalytics(days);
  if (!a) return <p className="muted">Couldn&apos;t load this page — the API rejected the request (e.g. your IP isn&apos;t on the staff allow-list) or didn&apos;t answer.</p>;
  return <Analytics a={a} />;
}
