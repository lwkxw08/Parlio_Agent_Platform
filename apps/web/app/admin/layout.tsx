import Link from "next/link";
import { fetchMe } from "@/lib/api";
import AdminTabs from "./tabs";
import { humanize } from "@/app/breakdown";

export const dynamic = "force-dynamic";

export default async function AdminLayout({ children }: { children: React.ReactNode }) {
  const me = await fetchMe();
  if (!me.ok) {
    return (
      <>
        <h1>Platform admin</h1>
        <p className="muted">{me.status === 401 ? <Link href="/login">Sign in</Link> : "API unreachable"}</p>
      </>
    );
  }
  if (!me.data.staff_role) {
    return (
      <>
        <h1>Platform admin</h1>
        <p className="muted">This area is for Parlio platform staff only.</p>
      </>
    );
  }
  return (
    <>
      <div className="row between">
        <h1>Platform admin</h1>
        <span className="pill">{humanize(me.data.staff_role)}</span>
      </div>
      <AdminTabs />
      {children}
    </>
  );
}
