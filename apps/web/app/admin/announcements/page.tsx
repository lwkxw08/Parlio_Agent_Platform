import { fetchAdminAnnouncements, fetchAdminFeedback, fetchAdminRoadmap, fetchMe, fetchWhiteGloveQueue } from "@/lib/api";
import Announcements from "./announcements";

export const dynamic = "force-dynamic";

export default async function Page() {
  const [me, ann, roadmap, feedback, wg] = await Promise.all([
    fetchMe(), fetchAdminAnnouncements(), fetchAdminRoadmap(), fetchAdminFeedback(), fetchWhiteGloveQueue(),
  ]);
  if (!ann || !roadmap || !feedback || !wg) return <p className="muted">Couldn&apos;t load this page — the API rejected the request (e.g. your IP isn&apos;t on the staff allow-list) or didn&apos;t answer.</p>;
  const role = me.ok ? me.data.staff_role : null;
  return <Announcements announcements={ann} roadmap={roadmap} feedback={feedback} whiteglove={wg} staffEmail={me.ok ? me.data.email : ""} canEdit={role === "owner" || role === "support"} />;
}
