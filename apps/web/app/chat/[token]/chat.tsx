"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { type ChatConfig, type ChatMessage, pollChat, sendChat } from "@/lib/api";

const VISITOR_KEY = "parlio-chat-visitor";
const NAME_KEY = "parlio-chat-name";

function visitorId(): string {
  try {
    const v = localStorage.getItem(VISITOR_KEY);
    if (v) return v;
    const fresh = `v-${crypto.randomUUID().replace(/-/g, "")}`;
    localStorage.setItem(VISITOR_KEY, fresh);
    return fresh;
  } catch {
    return `v-${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`;
  }
}

export default function Chat({ token, cfg }: { token: string; cfg: ChatConfig }) {
  const [visitor, setVisitor] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const bodyRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    document.documentElement.classList.add("embed");
    setVisitor(visitorId());
    try { setName(localStorage.getItem(NAME_KEY) ?? ""); } catch { /* storage blocked */ }
    return () => document.documentElement.classList.remove("embed");
  }, []);

  const merge = useCallback((incoming: ChatMessage[]) => {
    setMessages((prev) => {
      const seen = new Set(prev.map((m) => m.id));
      const add = incoming.filter((m) => !seen.has(m.id));
      return add.length ? [...prev, ...add].sort((a, b) => a.created_at.localeCompare(b.created_at)) : prev;
    });
  }, []);

  useEffect(() => {
    if (!visitor) return;
    let stop = false;
    const tick = async () => {
      const r = await pollChat(token, visitor);
      if (!stop && r) merge(r);
    };
    void tick();
    const t = setInterval(tick, 3000);
    return () => { stop = true; clearInterval(t); };
  }, [visitor, token, merge]);

  useEffect(() => {
    bodyRef.current?.scrollTo({ top: bodyRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  const send = async () => {
    const body = text.trim();
    if (!visitor || !body || busy) return;
    setBusy(true); setErr(null);
    try { if (name.trim()) localStorage.setItem(NAME_KEY, name.trim()); } catch { /* storage blocked */ }
    const r = await sendChat(token, visitor, body, name.trim() || undefined);
    setBusy(false);
    if (!r) { setErr("Couldn't send — please try again."); return; }
    setText("");
    merge(r);
  };

  return (
    <div className="chat-embed" style={{ ["--chat-accent" as string]: cfg.colour }}>
      <header className="chat-head">
        <strong>{cfg.title}</strong>
        <span className="small">Typically replies in seconds</span>
      </header>
      <div className="chat-body" ref={bodyRef}>
        <div className="msg assistant"><span>{cfg.greeting}</span></div>
        {messages.map((m) => (
          <div key={m.id} className={`msg ${m.direction === "in" ? "user" : "assistant"}`}>
            {m.direction !== "in" && m.author === "agent" && <span className="who small">{m.author_name ?? "Team"}</span>}
            <span style={{ whiteSpace: "pre-wrap" }}>{m.text}</span>
          </div>
        ))}
        {busy && <div className="msg assistant muted small">…</div>}
      </div>
      <form className="chat-compose" onSubmit={(e) => { e.preventDefault(); void send(); }}>
        {!messages.length && (
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Your name (optional)" maxLength={80} aria-label="Your name" />
        )}
        <div className="chat-row">
          <input
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Type a message…"
            maxLength={2000}
            disabled={busy || !visitor}
            autoFocus
            aria-label="Message"
          />
          <button type="submit" className="primary" disabled={busy || !text.trim() || !visitor}>Send</button>
        </div>
        {err && <span className="small" style={{ color: "var(--bad-fg)" }}>{err}</span>}
        <span className="small muted chat-foot">Powered by Parlio</span>
      </form>
    </div>
  );
}
