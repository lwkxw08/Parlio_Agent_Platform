import { fetchIncidents, fetchMe, fetchOpsOverview } from "@/lib/api";
import Ops from "./ops";

export const dynamic = "force-dynamic";

export default async function Page() {
  const [me, overview, incidents] = await Promise.all([fetchMe(), fetchOpsOverview(), fetchIncidents()]);
  if (!overview) return <p className="muted">API unreachable</p>;
  const role = me.ok ? me.data.staff_role : null;
  return <Ops overview={overview} incidents={incidents ?? []} canAct={role === "owner" || role === "support"} />;
}
