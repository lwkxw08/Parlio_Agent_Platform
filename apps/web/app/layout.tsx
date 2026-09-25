import type { Metadata } from "next";
import { fetchMe, fetchPublicStatus } from "@/lib/api";
import Sidebar, { type Account } from "./sidebar";
import HelpDrawer from "./help";
import AuthGate from "./auth-gate";
import { StatusBanner, TrialBanner, ViewAsBanner } from "./banners";
import "./globals.css";

export const metadata: Metadata = {
  title: "ParlioTec",
  description: "ParlioTec AI phone assistant dashboard",
  icons: { icon: "/logo-icon.png" },
};

export const dynamic = "force-dynamic";

// Applies the saved theme before first paint to avoid a light/dark flash. The account preference
// (rendered server-side) wins over the browser's remembered value.
const themeInit = (account: string | null) =>
  account
    ? `try{localStorage.setItem("parlio-theme",${JSON.stringify(account)})}catch(e){}`
    : `try{var t=localStorage.getItem("parlio-theme");if(t==="dark"||t==="light")document.documentElement.dataset.theme=t}catch(e){}`;

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
  const theme = me.ok ? me.data.theme : null;
  return (
    <html lang="en" data-theme={theme ?? "light"} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeInit(theme) }} />
      </head>
      <body>
        <Sidebar account={account} />
        <main>
          {status?.active && <StatusBanner status={status} />}
          {account.kind === "user" && account.viewAs && <ViewAsBanner tenant={account.viewAs} />}
          <AuthGate signedOut={account.kind === "signin"} checkCookie={account.kind === "user" && !account.dev}>
            {account.kind === "user" && account.tenantId && !account.viewAs && !account.staff ? (
              <TrialBanner tenant={account.tenantId}>{children}</TrialBanner>
            ) : (
              children
            )}
          </AuthGate>
        </main>
        {account.kind === "user" && <HelpDrawer />}
      </body>
    </html>
  );
}
