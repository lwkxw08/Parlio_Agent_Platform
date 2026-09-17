import { fetchAssistants, fetchSiteRollup, fetchSites } from "@/lib/api";
import SitesView from "./view";

export const dynamic = "force-dynamic";

export default async function Sites({ searchParams }: { searchParams: Promise<{ days?: string }> }) {
  const sp = await searchParams;
  const days = Math.min(365, Math.max(1, Number(sp.days ?? 30) || 30));
  const [assistants, sites, rollup] = await Promise.all([fetchAssistants(), fetchSites(), fetchSiteRollup(days)]);
  if (!assistants) return <><h1>Locations</h1><p className="muted">API unreachable</p></>;
  return <SitesView assistants={assistants} initialSites={sites ?? []} rollup={rollup} days={days} />;
}
