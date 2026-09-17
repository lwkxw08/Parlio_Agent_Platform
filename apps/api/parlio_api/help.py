"""In-app user guide and "Ask Parlio" help assistant.

The guide is a folder of markdown pages (``docs/guide/*.md``), one per dashboard screen, each
with YAML-ish front matter (title, route, summary, keywords) and ``##`` sections that mirror the
on-screen headings. :class:`Guide` indexes those sections; :class:`HelpAssistant` answers a
question by retrieving the most relevant sections and, when an OpenAI key is configured, asking
the model to write a short grounded answer with citations. Without a key (or if the model fails)
the top section is returned verbatim, so the feature always works.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

_STOP = {
    "the", "and", "for", "you", "your", "can", "how", "what", "with", "that", "this", "are",
    "does", "when", "where", "which", "from", "into", "have", "has", "will", "our", "not",
    "all", "any", "set", "use", "get", "make", "way", "want", "need", "should", "there",
    "about", "than", "then", "them", "they", "its", "also", "each", "one", "only", "more",
    "parlio", "assistant",
}  # fmt: skip

_SYNONYMS: dict[str, list[str]] = {
    "anonymous": ["withheld", "screening"],
    "withheld": ["anonymous", "screening"],
    "private": ["withheld", "anonymous"],
    "unknown": ["screening"],
    "block": ["blocked", "reject", "screening"],
    "blocking": ["blocked", "reject", "screening"],
    "spam": ["screening", "robocall"],
    "nuisance": ["spam", "screening"],
    "text": ["sms"],
    "texts": ["sms"],
    "texting": ["sms"],
    "message": ["sms"],
    "diary": ["calendar"],
    "appointment": ["booking", "bookings"],
    "appointments": ["bookings", "reminders"],
    "reminder": ["reminders"],
    "divert": ["forwarding", "forward"],
    "forward": ["forwarding", "divert"],
    "voice": ["voices", "accent"],
    "greeting": ["persona", "closed-hours"],
    "hours": ["business", "opening", "closed-hours"],
    "closed": ["closed-hours", "after-hours"],
    "holiday": ["holidays", "closures"],
    "night": ["closed-hours", "after-hours"],
    "recording": ["record", "consent"],
    "record": ["recording", "consent"],
    "transfer": ["transfers", "destinations", "department"],
    "colleague": ["destinations", "transfers"],
    "mobile": ["destinations", "phone", "forwarding"],
    "staff": ["team", "destinations"],
    "user": ["team", "invite"],
    "users": ["team", "invite"],
    "colleagues": ["team", "invite"],
    "login": ["sign-in", "security", "team"],
    "password": ["security", "2fa"],
    "delete": ["erase", "erasure", "retention"],
    "gdpr": ["erasure", "retention", "compliance"],
    "price": ["plan", "billing"],
    "cost": ["plan", "billing", "usage"],
    "pay": ["billing", "plan"],
    "invoice": ["billing"],
    "upgrade": ["plan", "billing"],
    "number": ["numbers", "billing"],
    "whatsapp": ["inbox", "channels"],
    "chat": ["web", "widget", "inbox"],
    "website": ["widget", "web"],
    "crm": ["connected", "apps", "hubspot"],
    "zapier": ["webhook", "connected"],
    "report": ["reports", "export", "scheduled"],
    "download": ["export", "csv"],
    "stats": ["analytics", "metrics"],
    "busy": ["demand", "volume", "hour"],
    "quiet": ["demand", "volume"],
    "test": ["simulate", "test callers", "synthetic"],
    "wrong": ["quality", "rules", "faqs"],
    "mistake": ["quality", "rules", "faqs"],
    "language": ["languages"],
    "spanish": ["languages"],
    "polish": ["languages"],
    "welsh": ["languages"],
    "faq": ["faqs"],
    "questions": ["faqs"],
    "answer": ["faqs", "rules"],
    "score": ["qa", "quality"],
    "listen": ["live", "recording"],
    "vip": ["classified", "contacts"],
    "customer": ["contacts", "status"],
    "customers": ["contacts", "status"],
    "urgent": ["keywords", "escalation", "no-answer"],
    "emergency": ["urgent", "on-call", "closed-hours"],
    "voicemail": ["no-answer", "message"],
    "slack": ["notifications", "rule"],
    "email": ["notifications", "rule", "report"],
    "notify": ["notifications"],
    "notified": ["notifications"],
    "alert": ["notifications", "alerts", "health"],
    "down": ["health", "status"],
    "broken": ["health", "status", "troubleshooting"],
    "working": ["health", "troubleshooting"],
    "publish": ["publish", "version"],
    "undo": ["version", "restore"],
    "revert": ["version", "restore"],
}

_FRONT = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9][a-z0-9'-]*", text.lower())
    return [t for t in words if len(t) > 2 and t not in _STOP]


class GuideSection(BaseModel):
    page: str
    page_title: str
    route: str
    heading: str
    anchor: str
    body: str

    @property
    def path(self) -> str:
        if self.heading == self.page_title:
            return self.page_title
        return f"{self.page_title} → {self.heading}"


class GuidePage(BaseModel):
    slug: str
    title: str
    route: str
    summary: str = ""
    keywords: list[str] = Field(default_factory=list)
    sections: list[GuideSection] = Field(default_factory=list)


class GuidePageSummary(BaseModel):
    slug: str
    title: str
    route: str
    summary: str
    sections: list[str]


class Citation(BaseModel):
    page: str
    title: str
    route: str
    heading: str
    anchor: str
    path: str


class HelpAnswer(BaseModel):
    answer: str
    citations: list[Citation]
    source: str  # "llm" | "guide" | "none"
    confident: bool


class HelpQuestion(BaseModel):
    question: str = Field(min_length=2, max_length=600)
    route: str | None = None
    history: list[dict[str, str]] = Field(default_factory=list, max_length=10)


@dataclass
class _Scored:
    section: GuideSection
    score: float
    hits: set[str] = field(default_factory=set)


def _candidate_dirs() -> list[Path]:
    out: list[Path] = []
    env = os.environ.get("PARLIO_GUIDE_DIR")
    if env:
        out.append(Path(env))
    here = Path(__file__).resolve()
    out.append(here.parents[3] / "docs" / "guide")  # repo checkout: apps/api/parlio_api/help.py
    out.append(Path("/app/docs/guide"))  # container
    return out


class Guide:
    """Loads and indexes the markdown guide."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or next((d for d in _candidate_dirs() if d.is_dir()), None)
        self.pages: list[GuidePage] = []
        self._sections: list[GuideSection] = []
        self._terms: dict[int, set[str]] = {}
        self._route_index: dict[str, str] = {}
        if self.directory is not None:
            self._load()

    # -- loading ---------------------------------------------------------------------------------

    def _load(self) -> None:
        assert self.directory is not None
        for path in sorted(self.directory.glob("*.md")):
            page = self._parse(path)
            if page is not None:
                self.pages.append(page)
        order = {"getting-started": 0, "faq": 99}
        self.pages.sort(key=lambda p: (order.get(p.slug, 50), p.title))
        for p in self.pages:
            self._route_index.setdefault(p.route, p.slug)
            for s in p.sections:
                idx = len(self._sections)
                self._sections.append(s)
                terms = set(_tokens(f"{s.heading} {s.heading} {s.body} {p.title}"))
                terms.update(_tokens(" ".join(p.keywords)))
                self._terms[idx] = terms

    @staticmethod
    def _parse(path: Path) -> GuidePage | None:
        raw = path.read_text(encoding="utf-8")
        meta: dict[str, str] = {}
        m = _FRONT.match(raw)
        if m:
            for line in m.group(1).splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip()
            raw = raw[m.end() :]
        title = meta.get("title") or path.stem.replace("-", " ").title()
        route = meta.get("route") or "/"
        slug = path.stem
        keywords = [k.strip() for k in meta.get("keywords", "").split(",") if k.strip()]
        sections: list[GuideSection] = []
        heading = title
        buf: list[str] = []

        def flush() -> None:
            body = "\n".join(buf).strip()
            if body:
                sections.append(
                    GuideSection(
                        page=slug,
                        page_title=title,
                        route=route,
                        heading=heading,
                        anchor=_slug(heading),
                        body=body,
                    )
                )

        for line in raw.splitlines():
            if line.startswith("## "):
                flush()
                heading = line[3:].strip()
                buf = []
            else:
                buf.append(line)
        flush()
        return GuidePage(
            slug=slug,
            title=title,
            route=route,
            summary=meta.get("summary", ""),
            keywords=keywords,
            sections=sections,
        )

    # -- queries ---------------------------------------------------------------------------------

    @property
    def loaded(self) -> bool:
        return bool(self.pages)

    def summaries(self) -> list[GuidePageSummary]:
        return [
            GuidePageSummary(
                slug=p.slug,
                title=p.title,
                route=p.route,
                summary=p.summary,
                sections=[s.heading for s in p.sections if s.heading != p.title],
            )
            for p in self.pages
        ]

    def page(self, slug: str) -> GuidePage | None:
        return next((p for p in self.pages if p.slug == slug), None)

    def page_for_route(self, route: str) -> GuidePage | None:
        route = "/" + route.strip("/").split("?")[0]
        best: tuple[int, str] | None = None
        for r, slug in self._route_index.items():
            matches = route == r or route.startswith(r.rstrip("/") + "/")
            if matches and (best is None or len(r) > best[0]):
                best = (len(r), slug)
        if best is None and route == "/":
            home = self._route_index.get("/setup")
            return self.page(home) if home else None
        return self.page(best[1]) if best else None

    def search(self, query: str, *, limit: int = 4, route: str | None = None) -> list[_Scored]:
        q = _tokens(query)
        expanded: dict[str, float] = {}
        for t in q:
            expanded[t] = max(expanded.get(t, 0), 1.0)
            for syn in _SYNONYMS.get(t, []):
                for st in _tokens(syn):
                    expanded[st] = max(expanded.get(st, 0), 0.7)
            if t.endswith("s") and len(t) > 4:
                expanded[t[:-1]] = max(expanded.get(t[:-1], 0), 0.9)
            else:
                expanded[t + "s"] = max(expanded.get(t + "s", 0), 0.9)
        if not expanded:
            return []
        current = self.page_for_route(route) if route else None
        scored: list[_Scored] = []
        for idx, s in enumerate(self._sections):
            terms = self._terms[idx]
            head = set(_tokens(s.heading))
            hits = {t for t in expanded if t in terms}
            if not hits:
                continue
            score = sum(expanded[t] for t in hits)
            score += sum(1.5 * expanded[t] for t in hits if t in head)
            if s.page == "faq":
                score *= 1.15  # FAQ entries are direct answers
            if current and s.page == current.slug:
                score *= 1.1
            scored.append(_Scored(s, score, hits))
        scored.sort(key=lambda x: -x.score)
        return scored[:limit]


class HelpAssistant:
    def __init__(
        self,
        guide: Guide,
        api_key: str | None,
        model: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.guide = guide
        self._client: httpx.AsyncClient | None = None
        if api_key:
            self._client = client or httpx.AsyncClient(
                base_url=base_url, headers={"Authorization": f"Bearer {api_key}"}, timeout=30
            )
        self._model = model

    async def ask(self, q: HelpQuestion) -> HelpAnswer:
        hits = self.guide.search(q.question, limit=4, route=q.route)
        if not hits:
            return HelpAnswer(
                answer=(
                    "I couldn't find that in the guide. Try different words, or open a support "
                    "ticket from the Support page and a person will help."
                ),
                citations=[],
                source="none",
                confident=False,
            )
        # FAQ entries make the best answer text, but the "open this setting" links should point at
        # the real screens they describe, so those come first.
        screens = [_cite(h.section) for h in hits if h.section.page != "faq"]
        citations = screens or [_cite(h.section) for h in hits]
        top = hits[0]
        confident = top.score >= 2.0 or (len(hits) > 1 and top.score >= 1.6 * hits[1].score)
        if self._client is not None:
            text = await self._llm(q, [h.section for h in hits])
            if text:
                used = [c for c in _cited_paths(text, hits) if c.page != "faq"]
                return HelpAnswer(
                    answer=text,
                    citations=used or citations[:2],
                    source="llm",
                    confident=confident,
                )
        body = _plain(top.section.body)
        answer = f"**{top.section.path}**\n\n{body}"
        return HelpAnswer(
            answer=answer, citations=citations[:3], source="guide", confident=confident
        )

    async def _llm(self, q: HelpQuestion, sections: list[GuideSection]) -> str | None:
        assert self._client is not None
        context = "\n\n".join(
            f"[{i + 1}] {s.path} (screen: {s.route})\n{s.body}" for i, s in enumerate(sections)
        )
        system = (
            "You are Parlio's in-app help. Answer the user's question about the Parlio dashboard "
            "using ONLY the guide excerpts provided. Be brief (2-5 sentences or a short numbered "
            "list). Give the exact screen path and setting name in bold, e.g. **Assistant Studio → "
            "Call screening**. If the excerpts don't answer it, say so and suggest opening a "
            "support ticket — never invent settings. End with the reference numbers you used, "
            "like [1] or [1][3]. Do not use markdown headings. British English."
        )
        messages: list[dict[str, str]] = [{"role": "system", "content": system}]
        for h in q.history[-6:]:
            role = h.get("role", "user")
            if role in ("user", "assistant") and h.get("content"):
                messages.append({"role": role, "content": h["content"][:1000]})
        messages.append(
            {
                "role": "user",
                "content": f"Guide excerpts:\n\n{context}\n\nQuestion: {q.question.strip()}",
            }
        )
        try:
            r = await self._client.post(
                "/chat/completions",
                json={"model": self._model, "temperature": 0.2, "messages": messages},
            )
            r.raise_for_status()
            out = str(r.json()["choices"][0]["message"]["content"]).strip()
            return out or None
        except Exception:
            log.warning("help LLM call failed; using guide text", exc_info=True)
            return None


def _cite(s: GuideSection) -> Citation:
    return Citation(
        page=s.page,
        title=s.page_title,
        route=s.route,
        heading=s.heading,
        anchor=s.anchor,
        path=s.path,
    )


def _cited_paths(text: str, hits: list[_Scored]) -> list[Citation]:
    nums = {int(n) for n in re.findall(r"\[(\d+)\]", text)}
    out: list[Citation] = []
    seen: set[tuple[str, str]] = set()
    for n in sorted(nums):
        if 1 <= n <= len(hits):
            s = hits[n - 1].section
            key = (s.page, s.anchor)
            if key not in seen:
                seen.add(key)
                out.append(_cite(s))
    return out


def _plain(md: str) -> str:
    md = re.sub(r"^\s*[-*]\s+", "• ", md, flags=re.M)
    return md.strip()
