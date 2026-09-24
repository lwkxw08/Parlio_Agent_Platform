# ruff: noqa: E501
"""Generate the marketing sample calls (public/audio/sample-<id>.mp3 + lib/sample-calls.json).

Fully synthetic: fictional businesses, callers, phone numbers and postcodes. Both parties are
Cartesia voices; the caller leg gets a narrow-band "phone line" filter so it sounds like
the far end of a call.

    CARTESIA_API_KEY=... python scripts/sample-call.py [heating|estate|recruitment ...]
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
AUDIO_DIR = ROOT / "public" / "audio"
OUT_JSON = ROOT / "lib" / "sample-calls.json"

VOICES = {
    "Gemma": "62ae83ad-4f6a-430b-af41-a9bede9286ca",
    "Ailsa": "fb02b554-7d64-4f90-841e-e57fc88f410c",
    "Oliver": "ee7ea9f8-c0c1-498c-9279-764d6b56d189",
    "Julia": "273f9ef7-9fc2-4def-88bb-ab108c6249ca",
    "Victoria": "dc30854e-e398-4579-9dc8-16f6cb2c19b9",
    "Archie": "ef191366-f52f-447a-a398-ed8c0f2943a1",
}


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    business: str
    ai_name: str
    caller_voice: str
    script: list[tuple[str, str]]


SCENARIOS: list[Scenario] = [
    Scenario(
        id="heating",
        title="Boiler repair booking",
        business="Northside Heating",
        ai_name="Gemma",
        caller_voice="Ailsa",
        script=[
            ("ai", "Good morning, Northside Heating, this is Gemma. How can I help you today?"),
            (
                "caller",
                "Oh hi. My boiler's stopped working this morning and there's no hot water. Can someone come out this week?",
            ),
            (
                "ai",
                "Sorry to hear that, I can sort that for you. Is anyone in the house vulnerable, or is there any smell of gas?",
            ),
            ("caller", "No, nothing like that. It's just not firing up."),
            (
                "ai",
                "Okay, that's a repair visit, which is booked as an hour. Tom's free on Thursday at ten, or Friday at two. Which suits you better?",
            ),
            ("caller", "Thursday at ten would be great."),
            ("ai", "Lovely. Can I take your name and the postcode for the property?"),
            ("caller", "It's Sarah Bennett, and the postcode is M20 4XY."),
            (
                "ai",
                "Thank you Sarah. And is the number you're calling from, ending nine one two three, the best one to text a confirmation to?",
            ),
            ("caller", "Yes, that's fine."),
            (
                "ai",
                "Perfect. You're booked with Tom on Thursday from ten till eleven for a boiler repair. I've sent you a text confirmation, and Tom will message when he's on his way. Is there anything else I can help with?",
            ),
            ("caller", "No, that's everything. Thanks so much."),
            ("ai", "You're welcome, Sarah. Have a good day, bye for now."),
        ],
    ),
    Scenario(
        id="estate",
        title="Property viewing",
        business="Harbourside Estates",
        ai_name="Oliver",
        caller_voice="Julia",
        script=[
            ("ai", "Good afternoon, Harbourside Estates, Oliver speaking. How can I help?"),
            (
                "caller",
                "Hi, I've seen the two-bedroom flat on Marine Parade on your website. Is it still available, and could I book a viewing?",
            ),
            (
                "ai",
                "It is still available, yes. That's the second-floor flat listed at two hundred and eighty-five thousand. I can book you in with Priya, who's handling that property. Were you thinking this week or the weekend?",
            ),
            ("caller", "Saturday morning if possible."),
            (
                "ai",
                "Priya has Saturday at ten thirty or eleven fifteen free. Which would you prefer?",
            ),
            ("caller", "Eleven fifteen, please."),
            (
                "ai",
                "Lovely. Can I take your name, and are you currently in a position to proceed, for example first-time buyer or selling a property?",
            ),
            (
                "caller",
                "It's James Whitfield. I'm a first-time buyer with a mortgage agreed in principle.",
            ),
            (
                "ai",
                "Thanks James, that's really helpful. And is the mobile you're calling from, ending four four seven eight, the best number for a confirmation text?",
            ),
            ("caller", "Yes, that's fine."),
            (
                "ai",
                "You're all set. Saturday at eleven fifteen at Marine Parade with Priya, and I've texted you the details and the parking note. Would you also like to hear about the similar flat that's just come on nearby?",
            ),
            ("caller", "Oh, go on then, send me the details."),
            (
                "ai",
                "Will do, I'll text the link across now. Thanks for calling James, see you Saturday. Bye for now.",
            ),
        ],
    ),
    Scenario(
        id="recruitment",
        title="Candidate interview slot",
        business="Brightpath Recruitment",
        ai_name="Victoria",
        caller_voice="Archie",
        script=[
            ("ai", "Hello, Brightpath Recruitment, this is Victoria. How can I help you today?"),
            (
                "caller",
                "Hi there. I applied for the warehouse team leader role in Leeds last week and got an email asking me to call to arrange an interview.",
            ),
            (
                "ai",
                "Great, thank you for calling back. Can I take your name so I can find your application?",
            ),
            ("caller", "Yeah, it's Daniel Okafor."),
            (
                "ai",
                "Thanks Daniel, I have you here for the team leader role. Rachel is running those interviews on Tuesday and Wednesday next week. She has Tuesday at two, Wednesday at nine thirty, or Wednesday at four.",
            ),
            ("caller", "Wednesday at nine thirty would suit me best."),
            (
                "ai",
                "Wednesday at nine thirty it is. It's a video interview, about forty-five minutes, and Rachel will ask about your shift management experience. Is the email on your application still the best one for the invitation?",
            ),
            (
                "caller",
                "Yes, that's right. Do I need to bring anything, or send anything over first?",
            ),
            (
                "ai",
                "Just have your right-to-work documents to hand so we can verify them on the call. I've also sent you a text with the time and the video link. Is there anything else I can help with?",
            ),
            ("caller", "No, that's great, thank you."),
            ("ai", "You're welcome, Daniel. Good luck on Wednesday, bye for now."),
        ],
    ),
]

GAP_MS = {"ai": 550, "caller": 700}


def tts(client: httpx.Client, key: str, voice: str, text: str) -> bytes:
    body: dict[str, object] = {
        "model_id": "sonic-3",
        "transcript": text,
        "voice": {"mode": "id", "id": voice},
        "language": "en",
        "output_format": {"container": "wav", "encoding": "pcm_s16le", "sample_rate": 44100},
    }
    r = client.post(
        "https://api.cartesia.ai/tts/bytes",
        json=body,
        headers={"X-API-Key": key, "Cartesia-Version": "2025-04-16"},
    )
    r.raise_for_status()
    return r.content


def duration_ms(path: Path) -> int:
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)]
    )
    return int(float(out) * 1000)


def render(client: httpx.Client, key: str, sc: Scenario) -> dict[str, object]:
    out_mp3 = AUDIO_DIR / f"sample-{sc.id}.mp3"
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        parts: list[Path] = []
        cues: list[dict[str, object]] = []
        t = 400
        parts.append(tmp / "lead.wav")
        subprocess.check_call(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=44100:cl=mono",
                "-t",
                "0.4",
                str(parts[0]),
            ]
        )
        for i, (who, text) in enumerate(sc.script):
            raw = tmp / f"{i:02d}.wav"
            voice = VOICES[sc.ai_name if who == "ai" else sc.caller_voice]
            raw.write_bytes(tts(client, key, voice, text))
            seg = tmp / f"{i:02d}-seg.wav"
            filt = (
                "highpass=f=300,lowpass=f=3400,acompressor=threshold=-18dB:ratio=3,volume=0.9"
                if who == "caller"
                else "volume=1.0"
            )
            subprocess.check_call(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-i",
                    str(raw),
                    "-af",
                    f"{filt},apad=pad_dur={GAP_MS[who] / 1000}",
                    "-ac",
                    "1",
                    "-ar",
                    "44100",
                    str(seg),
                ]
            )
            d = duration_ms(raw)
            cues.append({"who": who, "text": text, "start": t, "end": t + d})
            t += duration_ms(seg)
            parts.append(seg)
        concat = tmp / "list.txt"
        concat.write_text("".join(f"file '{p}'\n" for p in parts))
        subprocess.check_call(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat),
                "-c:a",
                "libmp3lame",
                "-b:a",
                "96k",
                str(out_mp3),
            ]
        )
    print(f"wrote {out_mp3} ({t / 1000:.1f}s)")
    return {
        "id": sc.id,
        "title": sc.title,
        "business": sc.business,
        "ai": sc.ai_name,
        "src": f"/audio/sample-{sc.id}.mp3",
        "duration": t,
        "cues": cues,
    }


def main() -> None:
    key = os.environ.get("CARTESIA_API_KEY")
    if not key:
        sys.exit("CARTESIA_API_KEY not set")
    wanted = set(sys.argv[1:]) or {s.id for s in SCENARIOS}
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    existing: dict[str, dict[str, object]] = {}
    if OUT_JSON.exists():
        existing = {str(s["id"]): s for s in json.loads(OUT_JSON.read_text())}
    with httpx.Client(timeout=60) as client:
        for sc in SCENARIOS:
            if sc.id in wanted:
                existing[sc.id] = render(client, key, sc)
    ordered = [existing[s.id] for s in SCENARIOS if s.id in existing]
    OUT_JSON.write_text(json.dumps(ordered, indent=1) + "\n")
    print(f"wrote {OUT_JSON}")


if __name__ == "__main__":
    main()
