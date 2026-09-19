"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { TOKEN_COOKIE } from "@/lib/api";

/** Pages reachable without a dashboard session (public tokens or sign-in itself). */
const PUBLIC_PREFIXES = ["/login", "/approve", "/chat", "/status", "/share", "/trust"];

export const isPublicPath = (p: string) => PUBLIC_PREFIXES.some((x) => p === x || p.startsWith(`${x}/`));

const hasTokenCookie = () => document.cookie.split(";").some((c) => c.trim().startsWith(`${TOKEN_COOKIE}=`));

/**
 * Redirects to /login when there is no session; renders nothing until then so no page leaks.
 * `signedOut` is the server's verdict at first load; afterwards the token cookie (which expires
 * with the Supabase session) is re-checked on every navigation and periodically, so an idle
 * session that lapses cannot keep browsing the shell.
 */
export default function AuthGate({ signedOut, checkCookie, children }: { signedOut: boolean; checkCookie: boolean; children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [expired, setExpired] = useState(false);
  const blocked = (signedOut || expired) && !isPublicPath(pathname);
  useEffect(() => {
    if (!checkCookie) return;
    const check = () => setExpired(!hasTokenCookie());
    check();
    const id = window.setInterval(check, 30_000);
    window.addEventListener("focus", check);
    return () => {
      window.clearInterval(id);
      window.removeEventListener("focus", check);
    };
  }, [checkCookie, pathname]);
  useEffect(() => {
    if (blocked) router.replace(`/login?next=${encodeURIComponent(pathname)}`);
  }, [blocked, pathname, router]);
  if (blocked) return null;
  return <>{children}</>;
}
