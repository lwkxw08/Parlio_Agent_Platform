"use client";

import { useEffect, useRef, useState } from "react";
import sample from "@/lib/sample-call.json";

type Cue = { who: "ai" | "caller"; text: string; start: number; end: number };

const CUES = sample.cues as Cue[];
const DURATION = sample.duration;
const SRC = "/audio/sample-call.mp3";

function mmss(ms: number) {
  const s = Math.max(0, Math.round(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** Pre-recorded, fully synthetic booking call with a transcript that follows the audio. */
export function SampleCall() {
  const audio = useRef<HTMLAudioElement | null>(null);
  const list = useRef<HTMLDivElement | null>(null);
  const [playing, setPlaying] = useState(false);
  const [pos, setPos] = useState(0);

  useEffect(() => {
    const a = new Audio(SRC);
    a.preload = "metadata";
    audio.current = a;
    let raf = 0;
    const tick = () => { setPos(a.currentTime * 1000); raf = requestAnimationFrame(tick); };
    a.addEventListener("play", () => { setPlaying(true); raf = requestAnimationFrame(tick); });
    a.addEventListener("pause", () => { setPlaying(false); cancelAnimationFrame(raf); setPos(a.currentTime * 1000); });
    a.addEventListener("ended", () => { setPlaying(false); cancelAnimationFrame(raf); setPos(0); a.currentTime = 0; });
    return () => { cancelAnimationFrame(raf); a.pause(); a.src = ""; audio.current = null; };
  }, []);

  const current = CUES.findIndex((c) => pos >= c.start && pos < c.end + 350);
  const reached = current >= 0 ? current : CUES.filter((c) => pos >= c.start).length - 1;

  useEffect(() => {
    const box = list.current;
    const el = box?.children[reached];
    if (!box || !(el instanceof HTMLElement)) return;
    box.scrollTo({ top: el.offsetTop - box.offsetTop - 12, behavior: "smooth" });
  }, [reached]);

  const toggle = () => {
    const a = audio.current;
    if (!a) return;
    if (a.paused) void a.play(); else a.pause();
  };

  const seek = (e: React.MouseEvent<HTMLDivElement>) => {
    const a = audio.current;
    if (!a) return;
    const r = e.currentTarget.getBoundingClientRect();
    const frac = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
    a.currentTime = (frac * DURATION) / 1000;
    setPos(frac * DURATION);
  };

  return (
    <div className="sample-call">
      <div className="sample-head">
        <button type="button" className={`play ${playing ? "on" : ""}`} onClick={toggle} aria-label={playing ? "Pause" : "Play sample call"}>
          {playing ? "❚❚" : "▶"}
        </button>
        <div className="meta">
          <b>Sample call · boiler repair booking</b>
          <span className="muted small">Northside Heating (fictional) · Gemma answers · {mmss(DURATION)}</span>
        </div>
        <span className="time">{mmss(pos)}</span>
      </div>
      <div className="track" onClick={seek} role="slider" aria-valuemin={0} aria-valuemax={DURATION} aria-valuenow={Math.round(pos)} tabIndex={0}>
        <div className="fill" style={{ width: `${(pos / DURATION) * 100}%` }} />
      </div>
      <div className="sample-lines" ref={list}>
        {CUES.map((c, i) => (
          <div key={i} className={`line ${c.who} ${i === reached ? "now" : ""} ${i < reached ? "done" : ""}`} onClick={() => { const a = audio.current; if (a) { a.currentTime = c.start / 1000; setPos(c.start); if (a.paused) void a.play(); } }}>
            <span className="who">{c.who === "ai" ? "Gemma" : "Caller"}</span>
            <span>{c.text}</span>
          </div>
        ))}
      </div>
      <p className="muted small sample-note">Generated for this demo with the same voice engine tenants use. Every name, number and postcode is fictional.</p>
    </div>
  );
}
