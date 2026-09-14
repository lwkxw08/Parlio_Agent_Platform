import { fetchMe, fetchStaff, fetchStaffSettings } from "@/lib/api";
import Staff from "./staff";

export const dynamic = "force-dynamic";

export default async function Page() {
  const [me, staff, settings] = await Promise.all([fetchMe(), fetchStaff(), fetchStaffSettings()]);
  if (!staff || !settings) return <p className="muted">API unreachable</p>;
  const meData = me.ok ? me.data : null;
  return <Staff staff={staff} settings={settings} isOwner={meData?.staff_role === "owner"} selfId={meData?.user_id ?? null} />;
}
