import { notFound } from "next/navigation";
import { fetchCall } from "@/lib/api";
import CallView from "./view";

export const dynamic = "force-dynamic";

type Search = { tab?: string; rec?: string; t?: string; seq?: string };

export default async function CallDetail({ params, searchParams }: { params: Promise<{ id: string }>; searchParams: Promise<Search> }) {
  const [{ id }, sp] = await Promise.all([params, searchParams]);
  const call = await fetchCall(id);
  if (!call) notFound();
  const t = Number(sp.t);
  const rec = Number(sp.rec);
  const seek = Number.isFinite(t) && sp.t !== undefined ? { index: Number.isFinite(rec) && sp.rec !== undefined ? rec : 0, offset_s: t } : null;
  return <CallView initial={call} initialTab={sp.tab} seek={seek} />;
}
