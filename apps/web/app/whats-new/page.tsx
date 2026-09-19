import Link from "next/link";
import { redirect } from "next/navigation";
import { fetchAnnouncements, fetchMe, fetchRoadmap } from "@/lib/api";
import WhatsNew from "./whats-new";

export const dynamic = "force-dynamic";

export default async function Page({ searchParams }: { searchParams: Promise<{ tenant?: string }> }) {
  const sp = await searchParams;
  const me = await fetchMe();
  if (!me.ok) return <><h1>What&apos;s new</h1><p className="muted">{me.status === 401 ? <Link href="/login">Sign in</Link> : "API unreachable"}</p></>;
  const tenant = sp.tenant ?? me.data.memberships.find((m) => m.status === "active")?.tenant_id;
  if (!tenant) redirect(me.data.staff_role ? "/admin" : "/onboarding");
  const [feed, roadmap] = await Promise.all([fetchAnnouncements(tenant), fetchRoadmap(tenant)]);
  if (!feed) return <><h1>What&apos;s new</h1><p className="muted">API unreachable</p></>;
  return <WhatsNew tenant={tenant} feed={feed} roadmap={roadmap ?? []} />;
}
