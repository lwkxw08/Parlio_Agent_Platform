import { fetchMe, fetchPlatformStatus } from "@/lib/api";
import Status from "./status";

export const dynamic = "force-dynamic";

export default async function Page() {
  const [me, status] = await Promise.all([fetchMe(), fetchPlatformStatus()]);
  if (!status) return <p className="muted">API unreachable</p>;
  const role = me.ok ? me.data.staff_role : null;
  return <Status status={status} canEdit={role === "owner" || role === "support"} />;
}
