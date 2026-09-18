"use client";

import { useEffect, useRef, useState } from "react";

type Turn = { who: "ai" | "caller"; text: string; pause: number };

const TURNS: Turn[] = [
  { who: "ai", text: "Good morning, Northside Heating, Gemma speaking. How can I help?", pause: 1400 },
  { who: "caller", text: "Hi — my boiler's stopped working, can someone come out this week?", pause: 1600 },
  { who: "ai", text: "Sorry to hear that. I can book a repair visit — that's an hour. Tom's free Thursday at 10 or Friday at 2. Which suits?", pause: 2000 },
  { who: "caller", text: "Thursday at 10 please.", pause: 1200 },
  { who: "ai", text: "Booked with Tom, Thursday 10 to 11. I'll text you a confirmation now — what's the postcode?", pause: 1800 },
];

const TYPING_MS = 1100;
const BOOKED_HOLD_MS = 4200;
const RESET_MS = 700;

/**
 * Hero mock-up: the transcript plays out one turn at a time (typing dots, then the bubble),
 * shows the booking confirmation, holds, and loops. With reduced motion the finished call is shown.
 */
export function LiveCall() {
  const [shown, setShown] = useState(0);
  const [typing, setTyping] = useState<Turn["who"] | null>(null);
  const [booked, setBooked] = useState(false);
  const [staticMode, setStaticMode] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setStaticMode(true);
      setShown(TURNS.length);
      setBooked(true);
      return;
    }
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const wait = (ms: number) => new Promise<void>((r) => { timer = setTimeout(r, ms); });
    (async () => {
      while (!cancelled) {
        setShown(0); setBooked(false); setTyping(null);
        await wait(600);
        for (let i = 0; i < TURNS.length && !cancelled; i++) {
          setTyping(TURNS[i].who);
          await wait(TYPING_MS);
          setTyping(null);
          setShown(i + 1);
          await wait(TURNS[i].pause);
        }
        if (cancelled) break;
        setBooked(true);
        await wait(BOOKED_HOLD_MS);
        await wait(RESET_MS);
      }
    })();
    return () => { cancelled = true; clearTimeout(timer); };
  }, []);

  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [shown, typing]);

  return (
    <div className="card live-call">
      <div className="call-head">
        <div className="avatar">G</div>
        <div>
          <b>Gemma · ParlioTec assistant</b>
          <div className="muted small">Incoming call · 07700 900123</div>
        </div>
        <span className="pill" style={{ marginLeft: "auto" }}><span className="dot" />Live</span>
      </div>
      <div className={`bubbles ${staticMode ? "static" : ""}`} ref={listRef} aria-live="off">
        {TURNS.slice(0, shown).map((t, i) => (
          <div key={i} className={`bubble ${t.who}`}>{t.text}</div>
        ))}
        {typing && (
          <div className={`bubble typing ${typing}`} aria-hidden="true">
            <span /><span /><span />
          </div>
        )}
      </div>
      <div className={`booked ${booked ? "in" : ""}`}>✓ Booked · Tom · Thu 10:00 · Boiler repair (60 min) · SMS sent</div>
      <div className="supervise">
        <span className="eye" aria-hidden="true" />
        <span>Your team can <b>listen</b>, <b>whisper</b> to Gemma or <b>take over</b> at any moment</span>
      </div>
    </div>
  );
}
