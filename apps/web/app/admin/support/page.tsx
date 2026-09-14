import { fetchDeskTickets, fetchMe, fetchTagReview } from "@/lib/api";
import Desk from "./desk";

export const dynamic = "force-dynamic";

export default async function Page({ searchParams }: { searchParams: Promise<{ tenant?: string }> }) {
  const sp = await searchParams;
  const [me, tickets, review] = await Promise.all([fetchMe(), fetchDeskTickets(), fetchTagReview(7)]);
  if (!tickets) return <p className="muted">API unreachable</p>;
  const role = me.ok ? me.data.staff_role : null;
  const staffName = me.ok ? me.data.email : "";
  return <Desk tickets={tickets} review={review} canAct={role === "owner" || role === "support"} tenantFilter={sp.tenant ?? null} staffName={staffName} />;
}
