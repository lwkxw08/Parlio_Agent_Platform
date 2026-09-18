# ruff: noqa: E501
"""Generate the marketing sample call (public/audio/sample-call.mp3 + sample-call.json).

Fully synthetic: fictional business, caller, phone number and postcode. Both parties are
Cartesia voices; the caller leg gets a narrow-band "phone line" filter so it sounds like
the far end of a call.

    CARTESIA_API_KEY=... python scripts/sample-call.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT_MP3 = ROOT / "public" / "audio" / "sample-call.mp3"
OUT_JSON = ROOT / "lib" / "sample-call.json"

GEMMA = "62ae83ad-4f6a-430b-af41-a9bede9286ca"
CALLER = "fb02b554-7d64-4f90-841e-e57fc88f410c"  # Ailsa

SCRIPT: list[tuple[str, str]] = [
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
]

GAP_MS = {"ai": 550, "caller": 700}


def tts(client: httpx.Client, key: str, voice: str, text: str, speed: float | None) -> bytes:
    body: dict[str, object] = {
        "model_id": "sonic-3",
        "transcript": text,
        "voice": {"mode": "id", "id": voice},
        "language": "en",
        "output_format": {"container": "wav", "encoding": "pcm_s16le", "sample_rate": 44100},
    }
    if speed is not None:
        body["speed"] = speed
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


def main() -> None:
    key = os.environ.get("CARTESIA_API_KEY")
    if not key:
        sys.exit("CARTESIA_API_KEY not set")
    OUT_MP3.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td, httpx.Client(timeout=60) as client:
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
        for i, (who, text) in enumerate(SCRIPT):
            raw = tmp / f"{i:02d}.wav"
            raw.write_bytes(tts(client, key, GEMMA if who == "ai" else CALLER, text, None))
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
                str(OUT_MP3),
            ]
        )
    OUT_JSON.write_text(json.dumps({"duration": t, "cues": cues}, indent=1) + "\n")
    print(f"wrote {OUT_MP3} ({t / 1000:.1f}s) and {OUT_JSON}")


if __name__ == "__main__":
    main()
