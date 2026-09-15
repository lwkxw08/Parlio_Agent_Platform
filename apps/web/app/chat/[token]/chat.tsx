"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { RemoteTrack, Room } from "livekit-client";
import {
  type ChatConfig,
  type ChatMessage,
  type ChatStatus,
  pollChatState,
  sendChat,
  startChatVoice,
} from "@/lib/api";

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

type VoiceState = "idle" | "connecting" | "connected" | "error";

/** Click-to-talk: joins the LiveKit room issued by the API; the same voice worker answers. */
function useVoice(token: string, visitor: string | null, name: string) {
  const [state, setState] = useState<VoiceState>("idle");
  const [muted, setMuted] = useState(false);
  const [simulated, setSimulated] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [seconds, setSeconds] = useState(0);
  const roomRef = useRef<Room | null>(null);
  const audioEls = useRef<HTMLMediaElement[]>([]);

  const cleanup = useCallback(() => {
    for (const el of audioEls.current) el.remove();
    audioEls.current = [];
    roomRef.current = null;
  }, []);

  useEffect(() => {
    if (state !== "connected") { setSeconds(0); return; }
    const t = setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, [state]);

  const hangUp = useCallback(async () => {
    const room = roomRef.current;
    cleanup();
    setMuted(false);
    setState("idle");
    if (room) await room.disconnect();
  }, [cleanup]);

  useEffect(() => () => { void roomRef.current?.disconnect(); cleanup(); }, [cleanup]);

  const start = useCallback(async () => {
    if (!visitor || state !== "idle") return;
    setState("connecting"); setError(null); setSimulated(false);
    const session = await startChatVoice(token, visitor, name.trim() || undefined, window.location.href.slice(0, 500));
    if (!session) { setState("error"); setError("Voice isn't available right now — please type instead."); return; }
    if (!session.url) {
      setSimulated(true);
      setState("connected");
      return;
    }
    try {
      const { Room: LKRoom, RoomEvent, Track } = await import("livekit-client");
      const room = new LKRoom({ adaptiveStream: false, dynacast: false });
      const attach = (track: RemoteTrack) => {
        if (track.kind !== Track.Kind.Audio) return;
        const el = track.attach();
        el.autoplay = true;
        document.body.appendChild(el);
        audioEls.current.push(el);
      };
      room.on(RoomEvent.TrackSubscribed, (track) => attach(track));
      room.on(RoomEvent.TrackUnsubscribed, (track) => {
        for (const el of track.detach()) { el.remove(); audioEls.current = audioEls.current.filter((x) => x !== el); }
      });
      room.on(RoomEvent.Disconnected, () => { if (roomRef.current === room) { cleanup(); setMuted(false); setState("idle"); } });
      roomRef.current = room;
      await room.connect(session.url, session.token);
      await room.localParticipant.setMicrophoneEnabled(true);
      await room.startAudio();
      setState("connected");
    } catch (e) {
      cleanup();
      setState("error");
      const msg = e instanceof Error ? e.message : "could not connect";
      setError(/permission|denied|NotAllowed/i.test(msg) ? "Microphone access was blocked — allow it in your browser and try again." : `Couldn't connect: ${msg}`);
    }
  }, [token, visitor, name, state, cleanup]);

  const toggleMute = useCallback(async () => {
    const next = !muted;
    setMuted(next);
    await roomRef.current?.localParticipant.setMicrophoneEnabled(!next);
  }, [muted]);

  const reset = useCallback(() => { setState("idle"); setError(null); }, []);

  return { state, muted, simulated, error, seconds, start, hangUp, toggleMute, reset };
}

const mmss = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

export default function Chat({ token, cfg }: { token: string; cfg: ChatConfig }) {
  const [visitor, setVisitor] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [status, setStatus] = useState<ChatStatus>("none");
  const [agentName, setAgentName] = useState<string | null>(null);
  const [department, setDepartment] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const voice = useVoice(token, visitor, name);

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
      const r = await pollChatState(token, visitor);
      if (stop || !r) return;
      merge(r.messages);
      setStatus(r.status);
      setAgentName(r.agent_name);
      setDepartment(r.department);
    };
    void tick();
    const t = setInterval(tick, status === "waiting" ? 2000 : 3000);
    return () => { stop = true; clearInterval(t); };
  }, [visitor, token, merge, status]);

  useEffect(() => {
    bodyRef.current?.scrollTo({ top: bodyRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, status]);

  const subtitle =
    status === "waiting" ? `Connecting you to ${department ?? "a team member"}…`
    : status === "human" ? `You're chatting with ${agentName ?? "the team"}`
    : status === "closed" ? "This chat has ended"
    : "Typically replies in seconds";

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
    const s = await pollChatState(token, visitor);
    if (s) { merge(s.messages); setStatus(s.status); setAgentName(s.agent_name); setDepartment(s.department); }
  };

  return (
    <div className="chat-embed" style={{ ["--chat-accent" as string]: cfg.colour }}>
      <header className="chat-head">
        <div className="row between">
          <strong>{cfg.title}</strong>
          {cfg.voice_enabled && voice.state === "idle" && (
            <button type="button" className="chat-talk" onClick={() => void voice.start()} disabled={!visitor} aria-label="Talk to us">
              Talk to us
            </button>
          )}
        </div>
        <span className="small">
          {voice.state === "connecting" && "Connecting…"}
          {voice.state === "connected" && (voice.simulated ? "Voice demo mode (no audio)" : `On a call · ${mmss(voice.seconds)}`)}
          {(voice.state === "idle" || voice.state === "error") && subtitle}
        </span>
        {(voice.state === "connected" || voice.state === "connecting") && (
          <div className="chat-row chat-call">
            {voice.state === "connected" && !voice.simulated && (
              <button type="button" className="chat-talk" onClick={() => void voice.toggleMute()} aria-pressed={voice.muted}>
                {voice.muted ? "Unmute" : "Mute"}
              </button>
            )}
            <button type="button" className="chat-talk danger" onClick={() => void voice.hangUp()}>
              {voice.state === "connecting" ? "Cancel" : "Hang up"}
            </button>
          </div>
        )}
        {voice.state === "error" && voice.error && (
          <div className="chat-row chat-call">
            <span className="small">{voice.error}</span>
            <button type="button" className="chat-talk" onClick={voice.reset}>Dismiss</button>
          </div>
        )}
      </header>
      <div className="chat-body" ref={bodyRef}>
        <div className="msg assistant"><span>{cfg.greeting}</span></div>
        {messages.map((m) =>
          m.author === "system" ? (
            <div key={m.id} className="chat-status" role="status">{m.text}</div>
          ) : (
            <div key={m.id} className={`msg ${m.direction === "in" ? "user" : "assistant"}`}>
              {m.direction !== "in" && m.author === "agent" && <span className="who small">{m.author_name ?? "Team"}</span>}
              <span style={{ whiteSpace: "pre-wrap" }}>{m.text}</span>
            </div>
          ),
        )}
        {busy && <div className="msg assistant muted small">…</div>}
        {status === "waiting" && !busy && (
          <div className="chat-status waiting" role="status" aria-live="polite">
            <span className="chat-dots" aria-hidden><i /><i /><i /></span>
            Waiting for {department ?? "a team member"} to join — usually a couple of minutes. You can keep typing.
          </div>
        )}
      </div>
      <form className="chat-compose" onSubmit={(e) => { e.preventDefault(); void send(); }}>
        {!messages.length && (
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Your name (optional)" maxLength={80} aria-label="Your name" />
        )}
        <div className="chat-row">
          <input
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={status === "closed" ? "Send a message to start a new chat…" : "Type a message…"}
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
