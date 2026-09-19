import { fetchMe, fetchPlatformStatus } from "@/lib/api";
import Status from "./status";

export const dynamic = "force-dynamic";

export default async function Page() {
  const [me, status] = await Promise.all([fetchMe(), fetchPlatformStatus()]);
  if (!status) return <p className="muted">Couldn&apos;t load this page — the API rejected the request (e.g. your IP isn&apos;t on the staff allow-list) or didn&apos;t answer.</p>;
  const role = me.ok ? me.data.staff_role : null;
  return <Status status={status} canEdit={role === "owner" || role === "support"} />;
}
