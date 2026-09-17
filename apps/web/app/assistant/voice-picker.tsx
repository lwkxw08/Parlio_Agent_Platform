"use client";

import { useEffect, useRef, useState } from "react";
import { type Voice, type VoiceCatalogue, type VoiceConfig, fetchVoices, previewVoice } from "@/lib/api";
import { humanize } from "@/app/breakdown";

type Props = { value: VoiceConfig; businessName: string; onChange: (v: VoiceConfig) => void };
type GenderFilter = "all" | "female" | "male";

/** Voice catalogue for the tenant's market: filter by accent/gender, listen, pick. The TTS engine
 *  is a platform decision (set in Platform admin), so the tenant only ever chooses a voice. */
export default function VoicePicker({ value, businessName, onChange }: Props) {
  const [cat, setCat] = useState<VoiceCatalogue | null>(null);
  const [gender, setGender] = useState<GenderFilter>("all");
  const [accent, setAccent] = useState<string | null>(null);
  const [playing, setPlaying] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showCustom, setShowCustom] = useState(false);
  const audio = useRef<HTMLAudioElement | null>(null);

  useEffect(() => { fetchVoices().then((c) => { if (!c) return; setCat(c); setAccent(c.accents[0] ?? null); }); }, []);
  useEffect(() => () => audio.current?.pause(), []);

  const provider = cat?.provider ?? value.provider;
  const all = cat?.voices ?? [];
  const accents = [...new Set([...(cat?.accents ?? []), ...all.map((v) => v.accent)])];
  const rank = (v: Voice) => { const i = (cat?.accents ?? []).indexOf(v.accent); return i < 0 ? 99 : i; };
  const voices = all
    .filter((v) => (accent === null || v.accent === accent) && (gender === "all" || v.gender === gender))
    .sort((a, b) => rank(a) - rank(b) || Number(b.recommended) - Number(a.recommended));
  const canPreview = cat?.preview_available ?? false;
  const selected = all.find((v) => v.id === value.voice_id);

  const stop = () => { audio.current?.pause(); audio.current = null; setPlaying(null); };
  const play = async (v: Voice) => {
    if (playing === v.id) return stop();
    stop(); setError(null); setPlaying(v.id);
    const r = await previewVoice({
      provider: v.provider, voice_id: v.id, speed: value.speed,
      text: `Good ${new Date().getHours() < 12 ? "morning" : "afternoon"}, thanks for calling ${businessName || "ParlioTec"}. You're through to ${v.name} - how can I help you today?`,
    });
    if (!r.ok) { setPlaying(null); return setError(r.error); }
    const a = new Audio(`data:${r.data.mime};base64,${r.data.audio_b64}`);
    audio.current = a;
    a.onended = () => setPlaying((p) => (p === v.id ? null : p));
    a.play().catch(() => setPlaying(null));
  };
  const pick = (v: Voice) => onChange({ ...value, provider: v.provider, voice_id: v.id });

  return (
    <div className="voice-picker">
      <div className="row" style={{ alignItems: "center", gap: "1rem", flexWrap: "wrap" }}>
        <div className="chips">
          <button type="button" className={accent === null ? "active" : ""} onClick={() => setAccent(null)}>All accents</button>
          {accents.map((a) => (
            <button key={a} type="button" className={accent === a ? "active" : ""} onClick={() => setAccent(a)}>{humanize(a)}</button>
          ))}
        </div>
        <div className="chips">
          {(["all", "female", "male"] as GenderFilter[]).map((g) => (
            <button key={g} type="button" className={gender === g ? "active" : ""} onClick={() => setGender(g)}>
              {g === "all" ? "All voices" : g === "female" ? "Female" : "Male"}
            </button>
          ))}
        </div>
        <span className="small muted">
          {selected ? <>Selected: <b>{selected.name}</b> ({humanize(selected.accent)} {selected.gender})</> : <>Selected: custom voice <code>{value.voice_id}</code></>}
        </span>
      </div>

      {!cat && <p className="small muted">Loading voices…</p>}
      {cat && voices.length === 0 && <p className="small muted">No voices match this filter.</p>}
      <div className="voice-grid">
        {voices.map((v) => {
          const isSel = v.id === value.voice_id;
          const isPlaying = playing === v.id;
          return (
            <div key={v.id} className={`card choice voice${isSel ? " selected" : ""}`} onClick={() => pick(v)} role="button" tabIndex={0}
              onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(v); } }}>
              <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
                <b>{v.name}</b>
                <span className="row" style={{ gap: "0.3rem" }}>
                  {v.recommended && <span className="pill accent">Recommended</span>}
                  <span className="pill">{v.gender === "female" ? "Female" : "Male"}</span>
                  <span className="pill">{humanize(v.accent)}</span>
                </span>
              </div>
              <p className="small muted" style={{ margin: "0.35rem 0 0.5rem" }}>{v.description}</p>
              <div className="row" style={{ gap: "0.5rem" }}>
                <button type="button" className="ghost" disabled={!canPreview} title={canPreview ? "Play a short sample" : "Previews are not enabled on this server"}
                  onClick={(e) => { e.stopPropagation(); void play(v); }}>
                  {isPlaying ? "■ Stop" : "▶ Preview"}
                </button>
                {isSel ? <span className="pill ok">In use</span> : <button type="button" className="ghost" onClick={(e) => { e.stopPropagation(); pick(v); }}>Use this voice</button>}
              </div>
            </div>
          );
        })}
      </div>
      {error && <p className="small" style={{ color: "var(--bad-fg)" }}>{error}</p>}
      {cat && !canPreview && <p className="small muted">Previews are not enabled on this server.</p>}

      <p className="small muted">
        Voice engine: <b>{provider === "cartesia" ? "Cartesia Sonic" : "ElevenLabs Flash"}</b> — managed by ParlioTec for your region
        {cat ? ` (${cat.market})` : ""}.
        {" "}
        <button type="button" className="ghost" onClick={() => setShowCustom((s) => !s)}>{showCustom ? "Hide" : "Advanced: use a custom voice ID"}</button>
      </p>
      {showCustom && (
        <label>Voice ID (paste from the voice library or a cloned voice on the same engine)
          <input value={value.voice_id} onChange={(e) => onChange({ ...value, provider, voice_id: e.target.value })} />
        </label>
      )}
    </div>
  );
}
