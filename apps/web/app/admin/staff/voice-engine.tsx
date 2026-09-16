"use client";

import { useState } from "react";
import { type Market, type VoicePlatformSettings, saveVoiceSettings, when } from "@/lib/api";

const PROVIDERS: [string, string][] = [
  ["cartesia", "Cartesia Sonic (lowest latency)"],
  ["elevenlabs", "ElevenLabs Flash"],
];

type Props = { settings: VoicePlatformSettings; markets: Market[]; isOwner: boolean };

/** Which TTS engine tenants get: a platform default plus optional per-market overrides. */
export default function VoiceEngine({ settings: initial, markets, isOwner }: Props) {
  const [s, setS] = useState<VoicePlatformSettings>(initial);
  const [msg, setMsg] = useState<string | null>(null);
  const flash = (m: string) => { setMsg(m); setTimeout(() => setMsg(null), 6000); };

  const setMarket = (code: string, provider: string) => {
    const next = { ...s.provider_by_market };
    if (provider === "") delete next[code]; else next[code] = provider;
    setS({ ...s, provider_by_market: next });
  };
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    const r = await saveVoiceSettings(s);
    if (!r.ok) return flash(r.error);
    setS(r.data);
    flash("Voice engine settings saved");
  };

  return (
    <form className="section form" onSubmit={save} style={{ marginTop: "1rem" }}>
      <h2>Voice engine</h2>
      <p className="hint">
        Tenants choose a voice, not the engine behind it. Pick the default text-to-speech provider here and override it per market if a
        provider sounds better (or costs less) for a region. Assistants on the wrong engine are moved to a recommended voice for their market
        the next time they are saved.
      </p>
      {msg && <p className="small" style={{ color: "var(--accent)" }}>{msg}</p>}
      <fieldset disabled={!isOwner} style={{ border: 0, padding: 0, margin: 0, display: "contents" }}>
        <div className="two">
          <label>Default provider
            <select value={s.default_provider} onChange={(e) => setS({ ...s, default_provider: e.target.value })}>
              {PROVIDERS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </label>
          <label>Default market for new tenants
            <select value={s.default_market} onChange={(e) => setS({ ...s, default_market: e.target.value })}>
              {markets.map((m) => <option key={m.code} value={m.code}>{m.name}</option>)}
            </select>
          </label>
        </div>
        <table>
          <thead><tr><th>Market</th><th>Leads with</th><th>Provider</th></tr></thead>
          <tbody>
            {markets.map((m) => (
              <tr key={m.code}>
                <td>{m.name} <span className="muted small">({m.code})</span></td>
                <td className="small muted">{m.accents.join(", ")} voices</td>
                <td>
                  <select value={s.provider_by_market[m.code] ?? ""} onChange={(e) => setMarket(m.code, e.target.value)}>
                    <option value="">Platform default</option>
                    {PROVIDERS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                  </select>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {isOwner && <button type="submit" className="primary">Save</button>}
      </fieldset>
      {s.updated_by && s.updated_at && <p className="muted small">Last changed by {s.updated_by} · {when(s.updated_at)}</p>}
    </form>
  );
}
