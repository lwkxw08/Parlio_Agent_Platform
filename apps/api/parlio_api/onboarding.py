"""Onboarding helpers: website analysis, Google Places enrichment and FAQ suggestions.

Everything degrades gracefully offline - the heuristics need no API keys; Places is used only
when `PARLIO_GOOGLE_PLACES_API_KEY` is set.
"""

from __future__ import annotations

import logging
import re
from html.parser import HTMLParser
from typing import Any

import httpx
from pydantic import BaseModel, Field

from parlio_api.store import CallRecord
from parlio_voice.models import BusinessInfo, Faq

log = logging.getLogger("parlio.api.onboarding")

_PHONE_RE = re.compile(r"(?:\+44\s?\d{2,4}|\(?0\d{2,4}\)?)[\s-]?\d{3,4}[\s-]?\d{3,4}")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_HOURS_RE = re.compile(
    r"\b(mon|tue|wed|thu|fri|sat|sun)[a-z]*\b[^\n]{0,40}?\d{1,2}(?::\d{2})?\s?(?:am|pm)?"
    r"\s?(?:-|\u2013|to)\s?\d{1,2}(?::\d{2})?\s?(?:am|pm)?",
    re.IGNORECASE,
)
_POSTCODE_RE = re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b")


class WebsiteAnalysis(BaseModel):
    url: str
    reachable: bool = True
    business_name: str | None = None
    business: BusinessInfo = Field(default_factory=BusinessInfo)
    opening_hours_text: list[str] = Field(default_factory=list)
    faqs: list[Faq] = Field(default_factory=list)
    headings: list[str] = Field(default_factory=list)
    error: str | None = None


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.description = ""
        self.headings: list[str] = []
        self.text: list[str] = []
        self.links: list[str] = []
        self._stack: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag in ("script", "style", "noscript", "svg"):
            self._skip += 1
        self._stack.append(tag)
        if tag == "meta" and (a.get("name") or a.get("property")) in (
            "description",
            "og:description",
        ):
            self.description = self.description or (a.get("content") or "")
        if tag == "a" and a.get("href"):
            self.links.append(a["href"] or "")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript", "svg") and self._skip:
            self._skip -= 1
        if self._stack and self._stack[-1] == tag:
            self._stack.pop()

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        t = " ".join(data.split())
        if not t:
            return
        if self._stack and self._stack[-1] == "title":
            self.title = self.title or t
        elif self._stack and self._stack[-1] in ("h1", "h2", "h3"):
            self.headings.append(t)
        self.text.append(t)


def analyse_html(url: str, html: str) -> WebsiteAnalysis:
    p = _TextExtractor()
    p.feed(html)
    body = "\n".join(p.text)
    name = (p.title.split("|")[0].split(" - ")[0].strip() or None) if p.title else None
    phones = [m.group(0).strip() for m in _PHONE_RE.finditer(body)]
    tel_links = [ln[4:].split("?")[0] for ln in p.links if ln.startswith("tel:")]
    mails = [ln[7:].split("?")[0] for ln in p.links if ln.startswith("mailto:")]
    mails += _EMAIL_RE.findall(body)
    services = [h for h in p.headings if 2 <= len(h.split()) <= 6 and not h.endswith("?")][:12]
    hours = list(dict.fromkeys(m.group(0).strip() for m in _HOURS_RE.finditer(body)))[:7]
    postcode = _POSTCODE_RE.search(body)
    address = None
    if postcode:
        idx = body.rfind("\n", 0, postcode.start())
        address = body[max(0, idx - 80) : postcode.end()].replace("\n", ", ").strip(", ")

    faqs: list[Faq] = []
    lines = p.text
    for i, line in enumerate(lines):
        if line.endswith("?") and 3 <= len(line.split()) <= 20 and i + 1 < len(lines):
            answer = lines[i + 1]
            if not answer.endswith("?") and len(answer) > 20:
                faqs.append(
                    Faq(category="website", question=line, answer=answer[:400], source="website")
                )
        if len(faqs) >= 10:
            break

    return WebsiteAnalysis(
        url=url,
        business_name=name,
        business=BusinessInfo(
            description=(p.description or " ".join(p.text[:3]))[:300],
            website=url,
            phone=next(iter(tel_links or phones), None),
            email=next(iter(mails), None),
            address=address,
            services=services,
        ),
        opening_hours_text=hours,
        faqs=faqs,
        headings=p.headings[:20],
    )


async def analyse_website(url: str, client: httpx.AsyncClient | None = None) -> WebsiteAnalysis:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    own = client is None
    client = client or httpx.AsyncClient(timeout=8.0, follow_redirects=True)
    try:
        r = await client.get(url, headers={"user-agent": "ParlioOnboarding/1.0"})
        r.raise_for_status()
        return analyse_html(str(r.url), r.text)
    except Exception as e:  # network errors are expected in offline/dev
        log.info("website analysis failed for %s: %s", url, e)
        return WebsiteAnalysis(url=url, reachable=False, error=str(e)[:200])
    finally:
        if own:
            await client.aclose()


class PlaceResult(BaseModel):
    place_id: str
    name: str
    address: str | None = None
    phone: str | None = None
    website: str | None = None
    rating: float | None = None
    opening_hours: list[str] = Field(default_factory=list)


async def search_places(
    query: str, api_key: str, client: httpx.AsyncClient | None = None
) -> list[PlaceResult]:
    """Google Places API (New) text search, UK-biased."""
    own = client is None
    client = client or httpx.AsyncClient(timeout=8.0)
    try:
        r = await client.post(
            "https://places.googleapis.com/v1/places:searchText",
            headers={
                "X-Goog-Api-Key": api_key,
                "X-Goog-FieldMask": "places.id,places.displayName,places.formattedAddress,"
                "places.nationalPhoneNumber,places.websiteUri,places.rating,"
                "places.regularOpeningHours.weekdayDescriptions",
            },
            json={"textQuery": query, "regionCode": "GB", "maxResultCount": 5},
        )
        r.raise_for_status()
        out: list[PlaceResult] = []
        for pl in r.json().get("places", []):
            out.append(
                PlaceResult(
                    place_id=pl["id"],
                    name=(pl.get("displayName") or {}).get("text", ""),
                    address=pl.get("formattedAddress"),
                    phone=pl.get("nationalPhoneNumber"),
                    website=pl.get("websiteUri"),
                    rating=pl.get("rating"),
                    opening_hours=(pl.get("regularOpeningHours") or {}).get(
                        "weekdayDescriptions", []
                    ),
                )
            )
        return out
    finally:
        if own:
            await client.aclose()


def suggest_faqs(calls: list[CallRecord], existing: list[Faq], limit: int = 8) -> list[Faq]:
    """Questions callers actually asked that no FAQ covers yet (heuristic, no LLM required)."""
    known = {f.question.lower().rstrip("?") for f in existing}
    counts: dict[str, int] = {}
    for c in calls:
        for item in c.transcript:
            if item.get("role") != "user":
                continue
            text = " ".join(str(item.get("text") or "").split())
            for sent in re.split(r"(?<=[.!?])\s+", text):
                q = sent.strip()
                if not q.endswith("?") or len(q.split()) < 4:
                    continue
                key = q.lower().rstrip("?")
                if key in known:
                    continue
                counts[key] = counts.get(key, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])[:limit]
    return [
        Faq(category="suggested", question=q.capitalize() + "?", answer="", source="suggested")
        for q, _ in ranked
    ]


def config_patch_from_analysis(a: WebsiteAnalysis) -> dict[str, Any]:
    """Fields the onboarding wizard pre-fills on the assistant config."""
    return {
        "business_name": a.business_name,
        "business": a.business.model_dump(),
        "faqs": [f.model_dump() for f in a.faqs],
    }
