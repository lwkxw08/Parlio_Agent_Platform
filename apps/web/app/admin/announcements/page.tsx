import { fetchAdminAnnouncements, fetchAdminFeedback, fetchAdminRoadmap, fetchMe, fetchWhiteGloveQueue } from "@/lib/api";
import Announcements from "./announcements";

export const dynamic = "force-dynamic";

export default async function Page() {
  const [me, ann, roadmap, feedback, wg] = await Promise.all([
    fetchMe(), fetchAdminAnnouncements(), fetchAdminRoadmap(), fetchAdminFeedback(), fetchWhiteGloveQueue(),
  ]);
  if (!ann || !roadmap || !feedback || !wg) return <p className="muted">API unreachable or not permitted</p>;
  const role = me.ok ? me.data.staff_role : null;
  return <Announcements announcements={ann} roadmap={roadmap} feedback={feedback} whiteglove={wg} staffEmail={me.ok ? me.data.email : ""} canEdit={role === "owner" || role === "support"} />;
}
