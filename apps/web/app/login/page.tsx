"use client";

import { useEffect, useState } from "react";
import { captureOAuthRedirect, sendPasswordReset, signInWithGoogle, signInWithPassword, signUp, supabaseConfigured } from "@/lib/auth";

export default function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [mode, setMode] = useState<"in" | "up">("in");
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (captureOAuthRedirect()) window.location.href = "/";
  }, []);

  if (!supabaseConfigured()) {
    return (
      <div className="section" style={{ maxWidth: 520, margin: "3rem auto" }}>
        <h2>Sign in</h2>
        <p className="hint">
          Authentication is in <b>dev mode</b>: the API trusts the seeded demo owner, so no sign-in is needed.
          Set <code>PARLIO_AUTH_MODE=supabase</code> on the API and <code>NEXT_PUBLIC_SUPABASE_URL</code> /
          <code>NEXT_PUBLIC_SUPABASE_ANON_KEY</code> on the dashboard to enable email + Google sign-in.
        </p>
        <a className="btn" href="/">Continue to dashboard</a>
      </div>
    );
  }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    const err = mode === "in" ? await signInWithPassword(email, password) : await signUp(email, password);
    setBusy(false);
    if (err) return setMsg(err);
    if (mode === "up") return setMsg("Check your inbox to confirm your email, then sign in.");
    window.location.href = "/";
  };

  return (
    <div className="section" style={{ maxWidth: 420, margin: "3rem auto" }}>
      <h2>{mode === "in" ? "Sign in to Parlio" : "Create your Parlio account"}</h2>
      <p className="hint">Use Google or your email address.</p>
      <button className="ghost" style={{ width: "100%", padding: "0.6rem" }} onClick={signInWithGoogle}>
        Continue with Google
      </button>
      <p className="muted small" style={{ textAlign: "center" }}>or</p>
      <form className="form" onSubmit={submit}>
        <label>Email <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} /></label>
        <label>Password <input type="password" required minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} /></label>
        <button className="primary" disabled={busy}>{mode === "in" ? "Sign in" : "Sign up"}</button>
      </form>
      {msg && <p className="muted small">{msg}</p>}
      <p className="small">
        {mode === "in" ? (
          <>
            New here? <a href="#" onClick={(e) => { e.preventDefault(); setMode("up"); }}>Create an account</a> ·{" "}
            <a href="#" onClick={async (e) => { e.preventDefault(); setMsg((await sendPasswordReset(email)) ?? "Reset email sent."); }}>Forgot password</a>
          </>
        ) : (
          <>Already registered? <a href="#" onClick={(e) => { e.preventDefault(); setMode("in"); }}>Sign in</a></>
        )}
      </p>
    </div>
  );
}
