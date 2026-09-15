"""Rewrites LLM text into something a TTS voice says clearly on a phone line.

The transcript keeps the LLM's wording; only the audio is affected. Handles the things callers
complained about: bulleted summaries spoken as one run-on sentence, phone numbers read as
"nine hundred and thirty-four", and postcodes rushed as words.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterable, AsyncIterator

_BULLET = re.compile(r"^\s*(?:[-*\u2022]|\d+[.)])\s+")
_MARKDOWN = re.compile(r"[*_`#]+")
_LABEL_COLON = re.compile(r"^([^:]{1,40}):\s+(.+)$")
_PHONE = re.compile(r"(?<![\w+])(?:\+?44|0)(?:[\s,.-]*\d){9,10}(?!\w)")
_POSTCODE = re.compile(
    r"\b([A-Z]{1,2}\d[A-Z\d]?)\s*(\d[A-Z]{2})\b",
)
_FLUSH_AT = re.compile(r"[.!?\n]\s*$")


def spell_digits(digits: str) -> str:
    groups: list[str]
    if digits.startswith("07") and len(digits) == 11:
        groups = [digits[:5], digits[5:8], digits[8:]]
    elif digits.startswith("02") and len(digits) == 11:
        groups = [digits[:3], digits[3:7], digits[7:]]
    elif digits.startswith("01") and len(digits) == 11:
        groups = [digits[:5], digits[5:8], digits[8:]]
    else:
        groups = [digits[i : i + 3] for i in range(0, len(digits), 3)]
    return ", ".join(" ".join(g) for g in groups)


def _phone(m: re.Match[str]) -> str:
    digits = re.sub(r"\D", "", m.group(0))
    if digits.startswith("44"):
        digits = "0" + digits[2:]
    return spell_digits(digits)


def _postcode(m: re.Match[str]) -> str:
    return f"{' '.join(m.group(1))}, {' '.join(m.group(2))}"


def speakable(text: str, *, digits: bool = True, postcodes: bool = True) -> str:
    """One paragraph -> short, separately-paused sentences with numbers spelt out."""
    text = _MARKDOWN.sub("", text)
    out: list[str] = []
    for raw in text.split("\n"):
        line = _BULLET.sub("", raw).strip()
        if not line:
            continue
        lm = _LABEL_COLON.match(line)
        if lm and not lm.group(1).lower().startswith(("to confirm", "so")):
            label, value = lm.group(1).strip(), lm.group(2).strip()
            label = label[0].upper() + label[1:]
            line = f"{label} is {value}"
        if line[-1] not in ".!?:,":
            line += "."
        out.append(line)
    joined = " ".join(out)
    if digits:
        joined = _PHONE.sub(_phone, joined)
    if postcodes:
        joined = _POSTCODE.sub(_postcode, joined)
    return joined


async def speakable_stream(
    text: AsyncIterable[str], *, digits: bool = True, postcodes: bool = True
) -> AsyncIterator[str]:
    """Buffer streamed LLM chunks to sentence/line boundaries, then rewrite each segment."""
    buf = ""
    async for chunk in text:
        buf += chunk
        if _FLUSH_AT.search(buf) or len(buf) > 240:
            yield speakable(buf, digits=digits, postcodes=postcodes) + " "
            buf = ""
    if buf.strip():
        yield speakable(buf, digits=digits, postcodes=postcodes)
