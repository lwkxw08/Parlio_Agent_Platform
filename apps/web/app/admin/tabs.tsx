"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const TABS = [
  ["/admin", "Overview"],
  ["/admin/analytics", "Analytics"],
  ["/admin/tenants", "Tenants"],
  ["/admin/plans", "Plans & coupons"],
  ["/admin/staff", "Staff"],
  ["/admin/ops", "Ops"],
  ["/admin/support", "Support desk"],
  ["/admin/status", "Banner"],
  ["/admin/announcements", "Announcements"],
  ["/admin/activity", "Activity"],
] as const;

export default function AdminTabs() {
  const path = usePathname();
  const active = (href: string) => (href === "/admin" ? path === "/admin" : path.startsWith(href));
  return (
    <div className="tabs">
      {TABS.map(([href, label]) => (
        <Link key={href} href={href} className={active(href) ? "active" : ""}>{label}</Link>
      ))}
    </div>
  );
}
