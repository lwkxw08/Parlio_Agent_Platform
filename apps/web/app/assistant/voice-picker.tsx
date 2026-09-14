"use client";

import { useEffect, useRef, useState } from "react";
import { type Voice, type VoiceCatalogue, type VoiceConfig, fetchVoices, previewVoice } from "@/lib/api";

type Props = { value: VoiceConfig; businessName: string; onChange: (v: VoiceConfig) => void };
type GenderFilter = "all" | "female" | "male";

/** Voice catalogue: filter by provider/gender, listen to a sample, pick. Custom IDs still allowed. */
export default function VoicePicker({ value, businessName, onChange }: Props) {
  const [cat, setCat] = useState<VoiceCatalogue | null>(null);
  const [gender, setGender] = useState<GenderFilter>("all");
  const [playing, setPlaying] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showCustom, setShowCustom] = useState(false);
  const audio = useRef<HTMLAudioElement | null>(null);

  useEffect(() => { fetchVoices().then((c) => setCat(c)); }, []);
  useEffect(() => () => audio.current?.pause(), []);

  const voices = (cat?.voices ?? []).filter((v) => v.provider === value.provider && (gender === "all" || v.gender === gender));
  const canPreview = cat?.preview_available[value.provider] ?? false;
  const selected = cat?.voices.find((v) => v.provider === value.provider && v.id === value.voice_id);

  const stop = () => { audio.current?.pause(); audio.current = null; setPlaying(null); };
  const play = async (v: Voice) => {
    if (playing === v.id) return stop();
    stop(); setError(null); setPlaying(v.id);
    const r = await previewVoice({
      provider: v.provider, voice_id: v.id, speed: value.speed,
      text: `Good ${new Date().getHours() < 12 ? "morning" : "afternoon"}, thanks for calling ${businessName || "Parlio"}. You're through to ${v.name} - how can I help you today?`,
    });
    if (!r.ok) { setPlaying(null); return setError(r.error); }
    const a = new Audio(`data:${r.data.mime};base64,${r.data.audio_b64}`);
    audio.current = a;
    a.onended = () => setPlaying((p) => (p === v.id ? null : p));
    a.play().catch(() => setPlaying(null));
  };
  const pick = (v: Voice) => onChange({ ...value, provider: v.provider, voice_id: v.id });
  const switchProvider = (p: string) => {
    const first = cat?.voices.find((v) => v.provider === p && v.recommended) ?? cat?.voices.find((v) => v.provider === p);
    onChange({ ...value, provider: p, voice_id: first?.id ?? value.voice_id });
  };

  return (
    <div className="voice-picker">
      <div className="row" style={{ alignItems: "flex-end", gap: "1rem", flexWrap: "wrap" }}>
        <label style={{ flex: "1 1 240px" }}>Voice provider
          <select value={value.provider} onChange={(e) => switchProvider(e.target.value)}>
            <option value="cartesia">Cartesia Sonic (lowest latency)</option>
            <option value="elevenlabs">ElevenLabs Flash</option>
          </select>
        </label>
        <div className="chips" style={{ paddingBottom: "0.4rem" }}>
          {(["all", "female", "male"] as GenderFilter[]).map((g) => (
            <button key={g} type="button" className={gender === g ? "active" : ""} onClick={() => setGender(g)}>
              {g === "all" ? "All voices" : g === "female" ? "Female" : "Male"}
            </button>
          ))}
        </div>
        <span className="small muted" style={{ paddingBottom: "0.5rem" }}>
          {selected ? <>Selected: <b>{selected.name}</b> ({selected.accent} {selected.gender})</> : <>Selected: custom voice <code>{value.voice_id}</code></>}
        </span>
      </div>

      {!cat && <p className="small muted">Loading voices…</p>}
      {cat && voices.length === 0 && <p className="small muted">No catalogue voices for this provider yet — paste a voice ID below.</p>}
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
                  <span className="pill">{v.accent}</span>
                </span>
              </div>
              <p className="small muted" style={{ margin: "0.35rem 0 0.5rem" }}>{v.description}</p>
              <div className="row" style={{ gap: "0.5rem" }}>
                <button type="button" className="ghost" disabled={!canPreview} title={canPreview ? "Play a short sample" : "Previews need the provider API key on the server"}
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
      {cat && !canPreview && <p className="small muted">Previews are off: add the {value.provider === "cartesia" ? "Cartesia" : "ElevenLabs"} API key to the API server to enable them.</p>}

      <p className="small">
        <button type="button" className="ghost" onClick={() => setShowCustom((s) => !s)}>{showCustom ? "Hide" : "Advanced: use a custom voice ID"}</button>
      </p>
      {showCustom && (
        <label>Voice ID (paste from the provider&apos;s voice library or a cloned voice)
          <input value={value.voice_id} onChange={(e) => onChange({ ...value, voice_id: e.target.value })} />
        </label>
      )}
    </div>
  );
}
