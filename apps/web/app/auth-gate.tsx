"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";

/** Pages reachable without a dashboard session (public tokens or sign-in itself). */
const PUBLIC_PREFIXES = ["/login", "/approve", "/chat", "/status", "/share", "/trust"];

export const isPublicPath = (p: string) => PUBLIC_PREFIXES.some((x) => p === x || p.startsWith(`${x}/`));

/** Redirects to /login when the API reports no session; renders nothing until then so no page leaks. */
export default function AuthGate({ signedOut, children }: { signedOut: boolean; children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const blocked = signedOut && !isPublicPath(pathname);
  useEffect(() => {
    if (blocked) router.replace(`/login?next=${encodeURIComponent(pathname)}`);
  }, [blocked, pathname, router]);
  if (blocked) return null;
  return <>{children}</>;
}
