import { fetchPublicApproval } from "@/lib/api";
import Decide from "./decide";

export const dynamic = "force-dynamic";

export default async function Approve({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  const a = await fetchPublicApproval(token);
  if (!a) {
    return <div className="approve-page section"><h2>Link not found</h2><p className="muted">This approval link is invalid, has already been used, or has expired.</p></div>;
  }
  return <Decide token={token} initial={a} />;
}
