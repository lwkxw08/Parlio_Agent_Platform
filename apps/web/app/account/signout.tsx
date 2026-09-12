"use client";

import { signOut } from "@/lib/auth";

export default function SignOut({ mode }: { mode: "dev" | "supabase" }) {
  if (mode === "dev") return <p className="muted small">Sign-out is not applicable in dev mode.</p>;
  return <button className="danger" onClick={signOut}>Sign out</button>;
}
