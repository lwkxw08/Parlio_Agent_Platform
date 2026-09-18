"use client";

import type { RemoteTrack, Room } from "livekit-client";
import { useCallback, useEffect, useRef, useState } from "react";
import { type DemoInfo, fetchSiteInfo, startDemoVoice } from "@/lib/api";
import { SampleCall } from "@/components/sample-call";

type VoiceState = "idle" | "connecting" | "connected" | "error";

const VISITOR_KEY = "parliotec-demo-visitor";

function visitorId(): string {
  try {
    const existing = window.localStorage.getItem(VISITOR_KEY);
    if (existing) return existing;
    const id = `v-${crypto.randomUUID().replace(/-/g, "").slice(0, 24)}`;
    window.localStorage.setItem(VISITOR_KEY, id);
    return id;
  } catch {
    return `v-${Math.random().toString(36).slice(2, 14)}${Date.now().toString(36)}`;
  }
}

function mmss(s: number) {
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

const MAX_SECONDS = 180;

/** "Hear it for yourself": joins the demo tenant's LiveKit room via the public site endpoint. */
export function HearItLive({ compact = false }: { compact?: boolean }) {
  const [info, setInfo] = useState<DemoInfo | null | undefined>(undefined);
  const [name, setName] = useState("");
  const [state, setState] = useState<VoiceState>("idle");
  const [muted, setMuted] = useState(false);
  const [simulated, setSimulated] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [seconds, setSeconds] = useState(0);
  const [mode, setMode] = useState<"listen" | "talk">("listen");
  const roomRef = useRef<Room | null>(null);
  const audioEls = useRef<HTMLMediaElement[]>([]);

  useEffect(() => {
    let alive = true;
    void fetchSiteInfo().then((s) => { if (alive) setInfo(s ? s.demo : null); });
    return () => { alive = false; };
  }, []);

  const cleanup = useCallback(() => {
    for (const el of audioEls.current) el.remove();
    audioEls.current = [];
    roomRef.current = null;
  }, []);

  const hangUp = useCallback(async () => {
    const room = roomRef.current;
    cleanup();
    setMuted(false);
    setState("idle");
    if (room) await room.disconnect();
  }, [cleanup]);

  useEffect(() => {
    if (state !== "connected") { setSeconds(0); return; }
    const t = setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, [state]);

  useEffect(() => {
    if (state === "connected" && seconds >= MAX_SECONDS) void hangUp();
  }, [seconds, state, hangUp]);

  useEffect(() => () => { void roomRef.current?.disconnect(); cleanup(); }, [cleanup]);

  const start = useCallback(async () => {
    if (state !== "idle") return;
    setState("connecting"); setError(null); setSimulated(false);
    const { session, error: err } = await startDemoVoice(visitorId(), name.trim() || undefined, window.location.href.slice(0, 500));
    if (!session) { setState("error"); setError(err ?? "The demo isn't available right now."); return; }
    if (!session.url) { setSimulated(true); setState("connected"); return; }
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
  }, [state, name, cleanup]);

  const toggleMute = useCallback(async () => {
    const next = !muted;
    setMuted(next);
    await roomRef.current?.localParticipant.setMicrophoneEnabled(!next);
  }, [muted]);

  const voiceOk = info?.voice_available ?? false;
  const live = state === "connected";
  const talkUnavailable = info === null || (info !== undefined && !voiceOk) || state === "error";

  return (
    <div className="demo-panel">
      <div>
        <div className="demo-tabs" role="tablist">
          <button type="button" role="tab" aria-selected={mode === "listen"} className={mode === "listen" ? "on" : ""} onClick={() => setMode("listen")} disabled={live || state === "connecting"}>Listen to a sample call</button>
          <button type="button" role="tab" aria-selected={mode === "talk"} className={mode === "talk" ? "on" : ""} onClick={() => setMode("talk")}>Talk to it yourself</button>
        </div>
        {mode === "listen" ? <SampleCall /> : (<>
        <div className={`demo-orb ${live ? "live" : ""}`} aria-live="polite">
          {state === "idle" && "Tap to talk"}
          {state === "connecting" && "Connecting…"}
          {live && (simulated ? "Demo mode" : mmss(seconds))}
          {state === "error" && "Unavailable"}
        </div>
        <div className="demo-controls">
          {state === "idle" && (
            <button type="button" className="btn primary lg" onClick={() => void start()} disabled={info === undefined || !voiceOk}>
              {info === undefined ? "Checking availability…" : voiceOk ? "Talk to the assistant" : "Live demo paused"}
            </button>
          )}
          {live && !simulated && (
            <button type="button" className="btn secondary" onClick={() => void toggleMute()} aria-pressed={muted}>{muted ? "Unmute" : "Mute"}</button>
          )}
          {(live || state === "connecting") && (
            <button type="button" className="btn danger" onClick={() => void hangUp()}>{state === "connecting" ? "Cancel" : "Hang up"}</button>
          )}
          {state === "error" && (
            <button type="button" className="btn secondary" onClick={() => { setState("idle"); setError(null); }}>Try again</button>
          )}
        </div>
        <div className="demo-status">
          {state === "error" && error}
          {state === "idle" && info === null && "The demo service is offline at the moment — please try later or request a call."}
          {state === "idle" && info && !info.voice_available && "The monthly demo allowance has been used — request a personal demo below."}
          {state === "idle" && voiceOk && "Uses your microphone. Up to 3 minutes; the call is recorded like a real one — please don't share real personal details."}
          {live && simulated && "The demo assistant is in text-simulation mode right now, so there is no audio."}
          {live && !simulated && "You're through to the demo plumbing business. Try booking a boiler service."}
        </div>
        {talkUnavailable && (
          <div className="demo-status">
            <button type="button" className="linkish" onClick={() => setMode("listen")}>Listen to a sample call instead →</button>
          </div>
        )}
        </>)}
      </div>
      <div>
        {!compact && (
          <>
            <div className="eyebrow">Hear it for yourself</div>
            <h2 style={{ marginBottom: "0.7rem" }}>Talk to a ParlioTec assistant right now</h2>
          </>
        )}
        {mode === "listen" ? (
          <p className="muted">
            A one-minute booking call, start to finish: the assistant checks for an emergency, offers real slots, takes the
            details and texts a confirmation — all in a natural British voice. Then try it yourself and ask it anything:
          </p>
        ) : (
          <p className="muted">
            This is a real ParlioTec assistant set up as a demo plumbing &amp; heating company — the same voice engine, booking
            rules and transfer logic your customers would get. Ask it anything; some ideas:
          </p>
        )}
        <ul className="demo-list">
          <li>&ldquo;My boiler&apos;s making a banging noise — can someone come out?&rdquo;</li>
          <li>&ldquo;Book me a boiler service next week, mornings only.&rdquo;</li>
          <li>&ldquo;Do you cover Manchester? How much is a callout?&rdquo;</li>
          <li>&ldquo;I can smell gas.&rdquo; — hear how emergencies are handled.</li>
        </ul>
        {mode === "talk" && state === "idle" && (
          <label className="field" style={{ marginTop: "1.2rem" }}>
            Your first name (optional — the assistant will use it)
            <input value={name} onChange={(e) => setName(e.target.value)} maxLength={80} placeholder="e.g. Sam" />
          </label>
        )}
        {info?.phone && (
          <div className="demo-phone">
            Prefer to dial? Call the demo line:
            <b>{info.phone}</b>
            <span className="muted small">Standard UK call charges apply. Calls are recorded for the demo.</span>
          </div>
        )}
      </div>
    </div>
  );
}
