import { fetchOverview, queryAnalytics, type Segment } from "@/lib/api";
import AnalyticsView from "./view";

export const dynamic = "force-dynamic";

const iso = (d: Date) => d.toISOString().slice(0, 10);

export default async function Analytics({ searchParams }: { searchParams: Promise<{ days?: string }> }) {
  const sp = await searchParams;
  const days = Math.min(365, Math.max(1, Number(sp.days ?? 30) || 30));
  const today = new Date();
  const end = iso(today);
  const start = iso(new Date(today.getTime() - (days - 1) * 86400000));
  const period: Segment = { start, end, hours: "all", days: "all", label: `Last ${days} days` };
  const compare: Segment = {
    start: iso(new Date(today.getTime() - (2 * days - 1) * 86400000)),
    end: iso(new Date(today.getTime() - days * 86400000)),
    hours: "all",
    days: "all",
    label: `Previous ${days} days`,
  };

  const [overview, initial] = await Promise.all([fetchOverview({ days }), queryAnalytics({ period, compare })]);
  if (!overview || !initial.ok) return <><h1>Analytics</h1><p className="muted">API unreachable</p></>;
  return <AnalyticsView overview={overview} initial={initial.data} />;
}
