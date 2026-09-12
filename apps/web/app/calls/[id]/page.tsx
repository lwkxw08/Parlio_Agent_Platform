import { notFound } from "next/navigation";
import { fetchCall } from "@/lib/api";
import CallView from "./view";

export const dynamic = "force-dynamic";

export default async function CallDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const call = await fetchCall(id);
  if (!call) notFound();
  return <CallView initial={call} />;
}
