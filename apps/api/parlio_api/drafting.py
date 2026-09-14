"""Ask AI to draft: turn a short brief into optimised wording for a Studio field.

The user types "write a description for Parlio Demo Plumbing, use www.example.co.uk"; we fetch
the website (if any URL is present), then ask the LLM for wording tuned to how that field is
used by the voice assistant. Without an OpenAI key a template-based fallback still produces a
usable draft from the website analysis / brief, so the button works in dev.
"""

from __future__ import annotations

import logging
import re
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from parlio_api.onboarding import WebsiteAnalysis, analyse_website
from parlio_voice.models import AssistantConfig

log = logging.getLogger("parlio.api.drafting")

DraftField = Literal[
    "description",
    "services",
    "persona_extra",
    "instructions",
    "greeting",
    "faq_answer",
    "rule",
    "sms_template",
]

# How each field is consumed by the assistant, so the LLM optimises for that use.
FIELD_GUIDANCE: dict[str, str] = {
    "description": (
        "A business description the phone assistant uses as background knowledge to answer"
        " callers. 2-4 sentences, plain UK English, factual: what the business does, who it"
        " serves, area covered, what makes it different. No marketing fluff, no first person."
    ),
    "services": (
        "A list of services the business offers, one per line, 2-6 words each, no numbering or"
        " bullets. Include what callers commonly ask for. Max 15 lines."
    ),
    "persona_extra": (
        "Extra persona guidance for a phone receptionist: 3-6 short imperative sentences about"
        " tone, things to always mention, things never to say or promise."
    ),
    "instructions": (
        "Core operating instructions for an AI phone receptionist: short numbered steps"
        " covering how to greet, what to collect, when to transfer or take a message, and"
        " what not to do. Max 12 lines."
    ),
    "greeting": (
        "A single spoken greeting sentence (max 20 words) a receptionist says when answering,"
        " naming the business and offering help. No emojis."
    ),
    "faq_answer": (
        "A spoken answer to a caller's question: 1-3 sentences, conversational, precise, no"
        " lists or markdown."
    ),
    "rule": (
        "A single clear instruction for the assistant describing a business rule (what to do"
        " when...). One or two sentences, imperative."
    ),
    "sms_template": (
        "An SMS text message from the business to a caller, under 300 characters, friendly,"
        " includes business name, may use {name} and {link} placeholders where relevant."
    ),
}

_URL_RE = re.compile(
    r"(?<![\w@.-])(?:https?://)?(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)+(?:/\S*)?", re.I
)
_URL_TLD_STOP = {"e.g", "i.e", "etc"}


class DraftRequest(BaseModel):
    field: DraftField
    brief: str = Field(min_length=3, max_length=2000)
    current: str = ""
    website: str | None = None
    context: str | None = None  # e.g. the FAQ question / rule name being answered


class Draft(BaseModel):
    field: DraftField
    text: str
    source: Literal["llm", "template"]
    website_used: str | None = None
    notes: list[str] = Field(default_factory=list)


def find_url(text: str) -> str | None:
    for m in _URL_RE.finditer(text):
        s = m.group(0).rstrip(".,;:)")
        if s.lower() in _URL_TLD_STOP or "@" in s:
            continue
        return s
    return None


def _site_context(site: WebsiteAnalysis | None) -> str:
    if site is None or not site.reachable:
        return ""
    b = site.business
    parts = [f"Website: {site.url}"]
    if site.business_name:
        parts.append(f"Name: {site.business_name}")
    if b.description:
        parts.append(f"Site description: {b.description}")
    if b.services:
        parts.append("Headings/services: " + "; ".join(b.services))
    if b.address:
        parts.append(f"Address: {b.address}")
    if site.opening_hours_text:
        parts.append("Hours: " + "; ".join(site.opening_hours_text))
    if site.faqs:
        parts.append("FAQs: " + " | ".join(f"{f.question} {f.answer[:120]}" for f in site.faqs[:5]))
    return "\n".join(parts)


def _assistant_context(cfg: AssistantConfig | None) -> str:
    if cfg is None:
        return ""
    b = cfg.business
    parts = [f"Business name: {cfg.business_name}"]
    if b.description:
        parts.append(f"Existing description: {b.description}")
    if b.services:
        parts.append("Existing services: " + ", ".join(b.services))
    if b.address:
        parts.append(f"Address: {b.address}")
    if cfg.persona.tone:
        parts.append(f"Persona tone: {cfg.persona.tone}, {cfg.persona.formality}")
    return "\n".join(parts)


def template_draft(
    req: DraftRequest, cfg: AssistantConfig | None, site: WebsiteAnalysis | None
) -> str:
    name = (site.business_name if site and site.reachable else None) or (
        cfg.business_name if cfg else "the business"
    )
    b = site.business if site and site.reachable else (cfg.business if cfg else None)
    services = list(b.services) if b else []
    area = b.address if b and b.address else None
    if req.field == "description":
        if b and b.description:
            out = f"{name} — {b.description.strip().rstrip('.')}."
        else:
            out = (
                f"{name} is a friendly, professional business that puts customers first. Our team"
                " handles enquiries promptly, gives clear straightforward advice and takes pride"
                " in reliable, high-quality work."
            )
        if services:
            out += " Services include " + ", ".join(services[:6]) + "."
        if area:
            out += f" Based at {area}."
        out += " Callers can leave their details and we'll get back to them as soon as possible."
        return out
    if req.field == "services":
        return "\n".join(services[:15]) if services else req.brief
    if req.field == "persona_extra":
        return (
            f"Speak as the receptionist for {name}. Be warm, concise and confident. "
            "Always confirm the caller's name and number before ending. "
            "Never quote firm prices or promise times without checking. "
            f"Context from the owner: {req.brief.strip()}"
        )
    if req.field == "instructions":
        return "\n".join(
            [
                f"1. Greet the caller and introduce yourself as the assistant for {name}.",
                "2. Find out the reason for the call and collect name and callback number.",
                "3. Answer questions using the business description, services and FAQs.",
                "4. Transfer or take a message when the caller asks for a person or the"
                " question is outside your knowledge.",
                "5. Do not quote prices or make commitments not covered by the FAQs.",
                f"Owner notes: {req.brief.strip()}",
            ]
        )
    if req.field == "greeting":
        return f"Thanks for calling {name}, how can I help you today?"
    if req.field == "faq_answer":
        q = f" about '{req.context}'" if req.context else ""
        return f"{req.brief.strip().rstrip('.')}. Let me know if you'd like more detail{q}."
    if req.field == "rule":
        return req.brief.strip().rstrip(".") + "."
    if req.field == "sms_template":
        body = req.brief.strip().rstrip(".")
        return f"Hi {{name}}, thanks for calling {name}. {body}. Reply if you need anything else."[
            :300
        ]
    return req.brief


class Drafter:
    def __init__(
        self,
        api_key: str | None,
        model: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1",
        client: httpx.AsyncClient | None = None,
        fetch_sites: bool = True,
    ) -> None:
        self._client: httpx.AsyncClient | None = None
        if api_key:
            self._client = client or httpx.AsyncClient(
                base_url=base_url, headers={"Authorization": f"Bearer {api_key}"}, timeout=30
            )
        self._model = model
        self._fetch_sites = fetch_sites

    async def draft(self, req: DraftRequest, cfg: AssistantConfig | None) -> Draft:
        url = req.website or find_url(req.brief)
        site: WebsiteAnalysis | None = None
        notes: list[str] = []
        if url and self._fetch_sites:
            site = await analyse_website(url)
            if not site.reachable:
                notes.append(f"Couldn't read {url}; drafted from your brief only.")
                site = None
        text = await self._llm(req, cfg, site) if self._client else None
        if text is None:
            notes.append(
                "AI drafting isn't configured on this server (no OpenAI key) - basic template used."
                if self._client is None
                else "The AI model didn't respond - basic template used; try again."
            )
            return Draft(
                field=req.field,
                text=template_draft(req, cfg, site),
                source="template",
                website_used=site.url if site else None,
                notes=notes,
            )
        return Draft(
            field=req.field,
            text=text,
            source="llm",
            website_used=site.url if site else None,
            notes=notes,
        )

    async def _llm(
        self, req: DraftRequest, cfg: AssistantConfig | None, site: WebsiteAnalysis | None
    ) -> str | None:
        assert self._client is not None
        system = (
            "You write configuration text for a UK AI phone receptionist. Return only the"
            " text to insert into the field - no preamble, no quotes, no markdown headings."
            f"\n\nField purpose: {FIELD_GUIDANCE[req.field]}"
        )
        user = f"Owner's brief: {req.brief.strip()}"
        if req.context:
            user += f"\nField context: {req.context}"
        if req.current.strip():
            user += f"\nCurrent text (improve or replace): {req.current.strip()[:1500]}"
        ac = _assistant_context(cfg)
        if ac:
            user += f"\n\nAssistant details:\n{ac}"
        sc = _site_context(site)
        if sc:
            user += f"\n\nFrom the business website (use these facts, don't invent others):\n{sc}"
        try:
            r = await self._client.post(
                "/chat/completions",
                json={
                    "model": self._model,
                    "temperature": 0.4,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
            )
            r.raise_for_status()
            out = str(r.json()["choices"][0]["message"]["content"]).strip()
            return out.strip('"') or None
        except Exception:
            log.warning("draft LLM call failed; using template", exc_info=True)
            return None
