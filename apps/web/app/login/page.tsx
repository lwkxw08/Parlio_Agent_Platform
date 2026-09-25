"use client";

import { useEffect, useState } from "react";
import { captureOAuthRedirect, resendConfirmation, sendPasswordReset, signInWithGoogle, signInWithPassword, signUp, supabaseConfigured } from "@/lib/auth";

/** Same-origin path to return to after sign-in (from ?next=), defaulting to the overview. */
function nextPath(): string {
  const n = new URLSearchParams(window.location.search).get("next");
  return n && n.startsWith("/") && !n.startsWith("//") ? n : "/";
}

export default function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [mode, setMode] = useState<"in" | "up" | "verify">("in");
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [resent, setResent] = useState(false);

  useEffect(() => {
    if (captureOAuthRedirect()) window.location.href = nextPath();
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
    setMsg(null);
    if (mode === "up") {
      const r = await signUp(email, password);
      setBusy(false);
      if ("error" in r) return setMsg(r.error);
      if (r.signedIn) window.location.href = nextPath();
      else setMode("verify");
      return;
    }
    const err = await signInWithPassword(email, password);
    setBusy(false);
    if (err) return setMsg(err);
    window.location.href = nextPath();
  };

  const resend = async () => {
    setBusy(true);
    const err = await resendConfirmation(email);
    setBusy(false);
    setMsg(err);
    setResent(!err);
  };

  if (mode === "verify") {
    return (
      <div className="section" style={{ maxWidth: 460, margin: "3rem auto", textAlign: "center" }}>
        <div style={{ fontSize: "2.5rem", lineHeight: 1 }} aria-hidden="true">✉</div>
        <h2>Verify your email address</h2>
        <p>
          We&apos;ve sent a confirmation link to <b>{email}</b>. Open the email and click the link to activate your
          account — then come back here to sign in and set up your assistant.
        </p>
        <p className="muted small">
          Not arrived after a minute or two? Check your spam or junk folder, and make sure the address above is right.
          If this address already has a ParlioTec account, no email is sent — sign in instead (use “Forgot password” if
          you need to).
        </p>
        {msg && <p className="muted small">{msg}</p>}
        {resent && !msg && <p className="muted small">Sent again — please check your inbox.</p>}
        <div style={{ display: "flex", gap: "0.6rem", justifyContent: "center", flexWrap: "wrap" }}>
          <button className="ghost" disabled={busy || resent} onClick={resend}>Resend email</button>
          <button className="primary" onClick={() => { setMode("in"); setMsg(null); setResent(false); }}>Go to sign in</button>
        </div>
        <p className="small">
          Wrong address? <a href="#" onClick={(e) => { e.preventDefault(); setMode("up"); setMsg(null); setResent(false); }}>Sign up again</a>
        </p>
      </div>
    );
  }

  return (
    <div className="section" style={{ maxWidth: 420, margin: "3rem auto" }}>
      <h2>{mode === "in" ? "Sign in to ParlioTec" : "Create your ParlioTec account"}</h2>
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
