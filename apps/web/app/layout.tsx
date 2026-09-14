import type { Metadata } from "next";
import { fetchMe, fetchPublicStatus } from "@/lib/api";
import Sidebar, { type Account } from "./sidebar";
import { StatusBanner, ViewAsBanner } from "./banners";
import "./globals.css";

export const metadata: Metadata = {
  title: "Parlio",
  description: "Parlio AI phone assistant dashboard",
  icons: { icon: "/logo-icon.png" },
};

export const dynamic = "force-dynamic";

// Applies the saved theme before first paint to avoid a light/dark flash.
const THEME_INIT = `try{var t=localStorage.getItem("parlio-theme");if(t==="dark"||t==="light")document.documentElement.dataset.theme=t}catch(e){}`;

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const [me, status] = await Promise.all([fetchMe(), fetchPublicStatus()]);
  const account: Account = me.ok
    ? {
        kind: "user",
        name: me.data.name ?? me.data.email,
        email: me.data.email,
        dev: me.data.mode === "dev",
        staff: me.data.staff_role != null,
        viewAs: me.data.view_as,
        tenantId: me.data.view_as ?? me.data.memberships.find((m) => m.status === "active")?.tenant_id ?? null,
      }
    : me.status === 401
      ? { kind: "signin" }
      : { kind: "offline" };
  return (
    <html lang="en" data-theme="light" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT }} />
      </head>
      <body>
        <Sidebar account={account} />
        <main>
          {status?.active && <StatusBanner status={status} />}
          {account.kind === "user" && account.viewAs && <ViewAsBanner tenant={account.viewAs} />}
          {children}
        </main>
      </body>
    </html>
  );
}
