import { fetchAdminAnalytics } from "@/lib/api";
import Analytics from "./analytics";

export const dynamic = "force-dynamic";

export default async function Page({ searchParams }: { searchParams: Promise<{ days?: string }> }) {
  const sp = await searchParams;
  const days = [7, 30, 90].includes(Number(sp.days)) ? Number(sp.days) : 30;
  const a = await fetchAdminAnalytics(days);
  if (!a) return <p className="muted">API unreachable</p>;
  return <Analytics a={a} />;
}
