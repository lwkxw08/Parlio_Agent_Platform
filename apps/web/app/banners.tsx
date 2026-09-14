"use client";

import { useRouter } from "next/navigation";
import { type PublicStatus, VIEW_AS_COOKIE } from "@/lib/api";

export function StatusBanner({ status }: { status: PublicStatus }) {
  const cls = status.level === "incident" ? "bad" : status.level === "maintenance" ? "" : "warn";
  return (
    <div className={`banner ${cls}`} role="status">
      <strong>{status.title || status.level}</strong>
      {status.message && <span>{status.message}</span>}
      {status.link && <a href={status.link} target="_blank" rel="noreferrer">More info</a>}
    </div>
  );
}

export function ViewAsBanner({ tenant }: { tenant: string }) {
  const router = useRouter();
  const exit = () => {
    document.cookie = `${VIEW_AS_COOKIE}=; path=/; max-age=0`;
    router.push(`/admin/tenants/${tenant}`);
    router.refresh();
  };
  return (
    <div className="banner warn" role="status">
      <strong>Viewing as {tenant}</strong>
      <span>Read-only support view — every request is logged.</span>
      <button type="button" className="ghost" onClick={exit}>Exit</button>
    </div>
  );
}
