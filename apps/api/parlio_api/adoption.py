"""Phase 19b — adoption: white-glove onboarding, first-week digest, FAQ import, announcements.

* :class:`WhiteGloveService`   Growth+ tenants request a configuration review / forwarding or SIP
  set-up / test-call session; platform staff work the queue from the admin console.
* :func:`first_week_report`    what the assistant did in the tenant's first days, rendered as a
  Setup-page card and folded into the day-7 check-in email.
* :func:`parse_faq_text` / :func:`parse_faq_csv`  turn pasted text, a CSV or a web page into
  reviewable FAQ suggestions; :func:`merge_faqs` applies the approved ones (new Studio version).
* :class:`AnnouncementService` staff-authored changelog / in-app announcements, a public roadmap
  with per-tenant votes, and free-text feedback.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from parlio_api.admin import PLATFORM_TENANT
from parlio_api.billing import BillingService
from parlio_api.calendar import CalendarService
from parlio_api.journey import QUESTIONNAIRE_KIND, setup_checklist
from parlio_api.notifications import NotificationEvent, NotificationService, NotifyEvent
from parlio_api.onboarding import analyse_website
from parlio_api.sip import SipService
from parlio_api.store import CallStore, TenantDoc
from parlio_api.value import ValueService
from parlio_voice.models import AssistantConfig, Faq, SmsTrigger

WHITEGLOVE_KIND = "whiteglove_request"
ANNOUNCEMENT_KIND = "announcement"
ANNOUNCEMENT_READ_KIND = "announcement_read"
ROADMAP_KIND = "roadmap_item"
ROADMAP_VOTE_KIND = "roadmap_vote"
FEEDBACK_KIND = "feedback"

WHITEGLOVE_ENTITLEMENT = "priority_support"


def _now() -> datetime:
    return datetime.now(UTC)


# -- white-glove onboarding -----------------------------------------------------------------------

WhiteGloveArea = Literal[
    "config_review", "forwarding", "sip", "test_calls", "integrations", "team_training"
]
WhiteGloveStatus = Literal["requested", "scheduled", "in_progress", "completed", "cancelled"]

AREA_LABELS: dict[str, str] = {
    "config_review": "Review my assistant configuration",
    "forwarding": "Help setting up call forwarding",
    "sip": "SIP trunk / PBX connection",
    "test_calls": "Run test calls with me",
    "integrations": "Connect calendar / CRM / helpdesk",
    "team_training": "Train my team on the dashboard",
}


class StaffNote(BaseModel):
    author: str
    text: str
    at: datetime = Field(default_factory=_now)


class WhiteGloveRequestIn(BaseModel):
    contact_name: str = Field(min_length=1, max_length=120)
    contact_email: str = Field(min_length=3, max_length=200)
    contact_phone: str | None = Field(default=None, max_length=40)
    areas: list[WhiteGloveArea] = Field(min_length=1)
    preferred_slots: list[str] = Field(default_factory=list, max_length=5)
    notes: str = Field(default="", max_length=2000)


class WhiteGloveRequest(WhiteGloveRequestIn):
    id: str = Field(default_factory=lambda: f"wg-{uuid4().hex[:10]}")
    tenant_id: str
    requested_by: str
    status: WhiteGloveStatus = "requested"
    assigned_to: str | None = None
    scheduled_at: datetime | None = None
    staff_notes: list[StaffNote] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class WhiteGloveUpdate(BaseModel):
    status: WhiteGloveStatus | None = None
    assigned_to: str | None = None
    scheduled_at: datetime | None = None
    note: str | None = Field(default=None, max_length=2000)


class WhiteGloveEligibility(BaseModel):
    eligible: bool
    reason: str | None = None
    open_request: WhiteGloveRequest | None = None


class WhiteGloveService:
    def __init__(
        self, store: CallStore, billing: BillingService, notifications: NotificationService
    ) -> None:
        self.store, self.billing, self.notifications = store, billing, notifications

    async def _save(self, req: WhiteGloveRequest) -> WhiteGloveRequest:
        req.updated_at = _now()
        await self.store.put_doc(
            TenantDoc(
                kind=WHITEGLOVE_KIND,
                id=req.id,
                tenant_id=req.tenant_id,
                data=req.model_dump(mode="json"),
                created_at=req.created_at,
            )
        )
        return req

    async def list(self, tenant_id: str | None = None, limit: int = 500) -> list[WhiteGloveRequest]:
        docs = await self.store.list_docs(WHITEGLOVE_KIND, tenant_id, limit)
        rows = [WhiteGloveRequest.model_validate(d.data) for d in docs]
        return sorted(rows, key=lambda r: r.created_at, reverse=True)

    async def get(self, request_id: str) -> WhiteGloveRequest | None:
        doc = await self.store.get_doc(WHITEGLOVE_KIND, request_id)
        return WhiteGloveRequest.model_validate(doc.data) if doc else None

    async def open_request(self, tenant_id: str) -> WhiteGloveRequest | None:
        for r in await self.list(tenant_id):
            if r.status not in ("completed", "cancelled"):
                return r
        return None

    async def eligibility(self, tenant_id: str) -> WhiteGloveEligibility:
        if not await self.billing.entitled(tenant_id, WHITEGLOVE_ENTITLEMENT):
            return WhiteGloveEligibility(
                eligible=False,
                reason="White-glove onboarding is included on Growth and Enterprise plans",
            )
        return WhiteGloveEligibility(eligible=True, open_request=await self.open_request(tenant_id))

    async def request(
        self, tenant_id: str, requested_by: str, body: WhiteGloveRequestIn
    ) -> WhiteGloveRequest:
        elig = await self.eligibility(tenant_id)
        if not elig.eligible:
            raise PermissionError(elig.reason or "not eligible")
        if elig.open_request is not None:
            raise ValueError("a white-glove session is already open for this organisation")
        req = WhiteGloveRequest(tenant_id=tenant_id, requested_by=requested_by, **body.model_dump())
        await self._save(req)
        cfgs = await self.store.list_assistants(tenant_id)
        await self.notifications.dispatch(
            NotificationEvent(
                tenant_id=tenant_id,
                company_id=cfgs[0].company_id if cfgs else None,
                event=NotifyEvent.TICKET_CREATED,
                title="White-glove onboarding requested",
                body=(
                    f"{body.contact_name} asked for: "
                    + ", ".join(AREA_LABELS[a] for a in body.areas)
                    + ". Our team will confirm a slot within one working day."
                ),
                context={"whiteglove_id": req.id},
            )
        )
        return req

    async def update(
        self, request_id: str, body: WhiteGloveUpdate, actor: str
    ) -> WhiteGloveRequest:
        req = await self.get(request_id)
        if req is None:
            raise KeyError(request_id)
        if body.status is not None:
            req.status = body.status
        if body.assigned_to is not None:
            req.assigned_to = body.assigned_to or None
        if body.scheduled_at is not None:
            req.scheduled_at = body.scheduled_at
            if req.status == "requested":
                req.status = "scheduled"
        if body.note:
            req.staff_notes.append(StaffNote(author=actor, text=body.note))
        return await self._save(req)

    async def cancel(self, tenant_id: str, request_id: str) -> WhiteGloveRequest:
        req = await self.get(request_id)
        if req is None or req.tenant_id != tenant_id:
            raise KeyError(request_id)
        req.status = "cancelled"
        return await self._save(req)


# -- first-week impact digest ---------------------------------------------------------------------


class FirstWeekReport(BaseModel):
    tenant_id: str
    signed_up_at: datetime | None
    days_live: int
    calls: int = 0
    answered: int = 0
    missed: int = 0
    after_hours: int = 0
    minutes: int = 0
    bookings: int = 0
    qualified_leads: int = 0
    attributed_pence: int = 0
    currency: str = "GBP"
    top_intents: list[tuple[str, int]] = Field(default_factory=list)
    checklist_completed: int = 0
    checklist_total: int = 0
    highlights: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=_now)


def _s(n: int) -> str:
    return "" if n == 1 else "s"


def _fmt_money(pence: int, currency: str) -> str:
    sym = {"GBP": "£", "EUR": "€", "USD": "$"}.get(currency, "")
    return f"{sym}{pence / 100:,.0f}"


async def first_week_report(
    tenant_id: str,
    store: CallStore,
    value: ValueService,
    billing: BillingService,
    sip: SipService,
    calendar: CalendarService,
    notifications: NotificationService,
    *,
    now: datetime | None = None,
) -> FirstWeekReport:
    now = now or _now()
    qdoc = await store.get_doc(QUESTIONNAIRE_KIND, tenant_id)
    raw = qdoc.data.get("signed_up_at") if qdoc else None
    signed = (
        datetime.fromisoformat(raw) if isinstance(raw, str) else (qdoc.created_at if qdoc else None)
    )
    days = max(0, (now - signed).days) if signed else 0

    rep = await value.attribute(tenant_id, 7)
    cl = await setup_checklist(tenant_id, store, billing, sip, calendar, notifications)
    cfgs = await store.list_assistants(tenant_id)
    cfg = cfgs[0] if cfgs else None

    calls = await store.list_calls(tenant_id, limit=500)
    if signed:
        calls = [c for c in calls if c.started_at >= signed]
    minutes = 0
    after_hours = 0
    for c in calls:
        if c.answered_at and c.ended_at:
            minutes += max(0, int((c.ended_at - c.answered_at).total_seconds() // 60))
        if cfg is not None and not cfg.is_open(c.started_at):
            after_hours += 1

    out = FirstWeekReport(
        tenant_id=tenant_id,
        signed_up_at=signed,
        days_live=days,
        calls=rep.calls,
        answered=rep.answered,
        missed=rep.missed,
        after_hours=after_hours,
        minutes=minutes,
        bookings=rep.bookings,
        qualified_leads=rep.qualified_leads,
        attributed_pence=rep.attributed_pence,
        currency=rep.currency,
        top_intents=sorted(rep.intents.items(), key=lambda kv: -kv[1])[:3],
        checklist_completed=cl.completed,
        checklist_total=cl.total,
    )
    name = cfg.name if cfg else "Your assistant"
    if out.answered:
        out.highlights.append(
            f"{name} answered {out.answered} call{'s' if out.answered != 1 else ''}"
        )
    if out.after_hours:
        out.highlights.append(f"{out.after_hours} of those were outside your opening hours")
    if out.bookings:
        out.highlights.append(
            f"{out.bookings} appointment{'s' if out.bookings != 1 else ''} booked"
        )
    if out.qualified_leads:
        out.highlights.append(
            f"{out.qualified_leads} qualified lead{_s(out.qualified_leads)} captured"
        )
    if out.attributed_pence:
        out.highlights.append(
            f"{_fmt_money(out.attributed_pence, out.currency)} of enquiries attributed "
            "to the assistant"
        )
    if not out.highlights:
        out.highlights.append("No calls yet — make a test call to see your first results here")
    if cl.next_step:
        out.next_steps.append(cl.next_step.title)
    has_booking = cfg is not None and any(
        sc.trigger == SmsTrigger.BOOKING_LINK and sc.enabled for sc in cfg.sms_scenarios
    )
    if out.calls and not out.bookings and not has_booking:
        out.next_steps.append("Add a booking link so callers can book without waiting")
    if out.top_intents and cfg is not None and len(cfg.faqs) < 5:
        out.next_steps.append("Import more FAQs so common questions are answered instantly")
    return out


def render_first_week(rep: FirstWeekReport, business: str) -> str:
    lines = [f"Your first {rep.days_live or 7} days with Parlio at {business}:"]
    lines += [f"• {h}" for h in rep.highlights]
    if rep.next_steps:
        lines.append("")
        lines.append("Suggested next steps:")
        lines += [f"• {s}" for s in rep.next_steps]
    return "\n".join(lines)


# -- guided FAQ import ----------------------------------------------------------------------------

FaqSource = Literal["text", "csv", "url"]

_Q_PREFIX = re.compile(r"^\s*(?:q(?:uestion)?\s*[:.\-)]|\d+[.)]|[-*•]|#+)\s*", re.I)
_A_PREFIX = re.compile(r"^\s*a(?:nswer)?\s*[:.\-)]\s*", re.I)


def _norm_q(q: str) -> str:
    return re.sub(r"\s+", " ", q.lower().strip().rstrip("?")).strip()


def _strip_q(line: str) -> str:
    return _Q_PREFIX.sub("", line).strip().strip("*_")


def parse_faq_text(text: str, category: str = "imported") -> list[Faq]:
    """Pull Q/A pairs from pasted text: ``Q:/A:`` blocks, ``question?`` + answer paragraph, or
    markdown headings ending in ``?`` followed by their body."""
    out: list[Faq] = []
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text.replace("\r", "")) if b.strip()]
    pending_q: str | None = None
    for block in blocks:
        lines = [ln.rstrip() for ln in block.split("\n") if ln.strip()]
        # explicit Q:/A:
        if any(_A_PREFIX.match(ln) for ln in lines):
            q, a = None, []
            for ln in lines:
                if _A_PREFIX.match(ln):
                    a.append(_A_PREFIX.sub("", ln).strip())
                elif q is None or not a:
                    q = _strip_q(ln) if q is None else f"{q} {_strip_q(ln)}"
                else:
                    a.append(ln.strip())
            if q and a:
                out.append(Faq(category=category, question=q, answer=" ".join(a), source="import"))
            continue
        first = _strip_q(lines[0])
        if pending_q is not None and not first.endswith("?"):
            out.append(
                Faq(category=category, question=pending_q, answer=" ".join(lines), source="import")
            )
            pending_q = None
            continue
        if first.endswith("?"):
            rest = " ".join(ln.strip() for ln in lines[1:]).strip()
            if rest:
                out.append(Faq(category=category, question=first, answer=rest, source="import"))
            else:
                pending_q = first
            continue
        # single block with several "question? answer." sentences on separate lines
        for i, ln in enumerate(lines):
            cand = _strip_q(ln)
            if (
                cand.endswith("?")
                and i + 1 < len(lines)
                and not _strip_q(lines[i + 1]).endswith("?")
            ):
                out.append(
                    Faq(
                        category=category,
                        question=cand,
                        answer=lines[i + 1].strip(),
                        source="import",
                    )
                )
    return _dedupe(out)


def parse_faq_csv(text: str, category: str = "imported") -> list[Faq]:
    """CSV/TSV with ``question``/``answer`` (and optional ``category``) columns, or the first two
    columns when there is no header."""
    sample = text[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return []
    header = [c.strip().lower() for c in rows[0]]
    qi = next((i for i, h in enumerate(header) if h in ("question", "q", "title")), None)
    ai = next((i for i, h in enumerate(header) if h in ("answer", "a", "response", "body")), None)
    ci = next((i for i, h in enumerate(header) if h in ("category", "topic", "section")), None)
    body = rows[1:] if qi is not None and ai is not None else rows
    qi, ai = (qi if qi is not None else 0), (ai if ai is not None else 1)
    out: list[Faq] = []
    for r in body:
        if len(r) <= max(qi, ai):
            continue
        q, a = r[qi].strip(), r[ai].strip()
        if not q or not a:
            continue
        cat = r[ci].strip() if ci is not None and len(r) > ci and r[ci].strip() else category
        out.append(
            Faq(
                category=cat,
                question=q if q.endswith("?") else q + "?",
                answer=a[:600],
                source="import",
            )
        )
    return _dedupe(out)


async def faqs_from_url(url: str) -> list[Faq]:
    analysis = await analyse_website(url)
    out = [
        Faq(category="website", question=f.question, answer=f.answer, source="import")
        for f in analysis.faqs
    ]
    return _dedupe(out)


def _dedupe(faqs: list[Faq]) -> list[Faq]:
    seen: set[str] = set()
    out: list[Faq] = []
    for f in faqs:
        k = _norm_q(f.question)
        if not k or k in seen or len(f.question) > 300:
            continue
        seen.add(k)
        out.append(f)
    return out


class FaqImportIn(BaseModel):
    source: FaqSource
    content: str = Field(min_length=1, max_length=200_000)
    category: str = Field(default="imported", max_length=40)


class FaqImportResult(BaseModel):
    source: FaqSource
    suggested: list[Faq]
    duplicates: list[Faq] = Field(
        default_factory=list, description="already covered by an existing FAQ"
    )


def review_import(new: list[Faq], existing: list[Faq]) -> FaqImportResult:
    known = {_norm_q(f.question) for f in existing}
    fresh = [f for f in new if _norm_q(f.question) not in known]
    dupes = [f for f in new if _norm_q(f.question) in known]
    return FaqImportResult(source="text", suggested=fresh, duplicates=dupes)


def merge_faqs(cfg: AssistantConfig, approved: list[Faq]) -> tuple[AssistantConfig, int]:
    """Append approved FAQs (skipping questions already present); returns the new config + count."""
    known = {_norm_q(f.question) for f in cfg.faqs}
    added: list[Faq] = []
    for f in approved:
        k = _norm_q(f.question)
        if k and k not in known and f.answer.strip():
            known.add(k)
            added.append(f)
    if not added:
        return cfg, 0
    return cfg.model_copy(update={"faqs": [*cfg.faqs, *added]}), len(added)


# -- announcements / changelog / roadmap / feedback -----------------------------------------------

AnnouncementKind = Literal["feature", "improvement", "fix", "notice"]


class AnnouncementIn(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    body: str = Field(min_length=1, max_length=8000)
    kind: AnnouncementKind = "feature"
    pinned: bool = False
    published: bool = True
    link: str | None = Field(default=None, max_length=400)


class Announcement(AnnouncementIn):
    id: str = Field(default_factory=lambda: f"ann-{uuid4().hex[:8]}")
    author: str
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class AnnouncementView(Announcement):
    read: bool = False


class AnnouncementFeed(BaseModel):
    unread: int
    items: list[AnnouncementView]


RoadmapStatus = Literal["considering", "planned", "in_progress", "shipped"]


class RoadmapItemIn(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=4000)
    status: RoadmapStatus = "considering"
    category: str = Field(default="general", max_length=40)
    eta: str | None = Field(default=None, max_length=40)


class RoadmapItem(RoadmapItemIn):
    id: str = Field(default_factory=lambda: f"rm-{uuid4().hex[:8]}")
    votes: int = 0
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class RoadmapView(RoadmapItem):
    voted: bool = False


FeedbackKind = Literal["idea", "bug", "praise", "other"]


class FeedbackIn(BaseModel):
    kind: FeedbackKind = "idea"
    text: str = Field(min_length=3, max_length=4000)
    page: str | None = Field(default=None, max_length=200)
    roadmap_item_id: str | None = None


class Feedback(FeedbackIn):
    id: str = Field(default_factory=lambda: f"fb-{uuid4().hex[:8]}")
    tenant_id: str
    author: str
    status: Literal["new", "reviewed", "planned", "closed"] = "new"
    created_at: datetime = Field(default_factory=_now)


class AnnouncementService:
    def __init__(self, store: CallStore) -> None:
        self.store = store

    # announcements ------------------------------------------------------------------------------
    async def _put_ann(self, a: Announcement) -> Announcement:
        a.updated_at = _now()
        await self.store.put_doc(
            TenantDoc(
                kind=ANNOUNCEMENT_KIND,
                id=a.id,
                tenant_id=PLATFORM_TENANT,
                data=a.model_dump(mode="json"),
                created_at=a.created_at,
            )
        )
        return a

    async def announcements(self, *, include_drafts: bool = False) -> list[Announcement]:
        docs = await self.store.list_docs(ANNOUNCEMENT_KIND, PLATFORM_TENANT, 500)
        rows = [Announcement.model_validate(d.data) for d in docs]
        if not include_drafts:
            rows = [a for a in rows if a.published]
        return sorted(rows, key=lambda a: (a.pinned, a.created_at), reverse=True)

    async def create_announcement(self, body: AnnouncementIn, author: str) -> Announcement:
        return await self._put_ann(Announcement(author=author, **body.model_dump()))

    async def update_announcement(self, ann_id: str, body: AnnouncementIn) -> Announcement:
        doc = await self.store.get_doc(ANNOUNCEMENT_KIND, ann_id)
        if doc is None:
            raise KeyError(ann_id)
        a = Announcement.model_validate(doc.data).model_copy(update=body.model_dump())
        return await self._put_ann(a)

    async def delete_announcement(self, ann_id: str) -> bool:
        return await self.store.delete_doc(ANNOUNCEMENT_KIND, ann_id)

    async def feed(self, tenant_id: str, user_id: str) -> AnnouncementFeed:
        reads = {
            d.data.get("announcement_id")
            for d in await self.store.list_docs(ANNOUNCEMENT_READ_KIND, tenant_id, 2000)
            if d.data.get("user_id") == user_id
        }
        items = [
            AnnouncementView(**a.model_dump(), read=a.id in reads)
            for a in await self.announcements()
        ]
        return AnnouncementFeed(unread=sum(1 for i in items if not i.read), items=items)

    async def mark_read(self, tenant_id: str, user_id: str, ann_id: str | None = None) -> int:
        ids = [ann_id] if ann_id else [a.id for a in await self.announcements()]
        n = 0
        for i in ids:
            key = f"{tenant_id}:{user_id}:{i}"
            if await self.store.get_doc(ANNOUNCEMENT_READ_KIND, key) is None:
                await self.store.put_doc(
                    TenantDoc(
                        kind=ANNOUNCEMENT_READ_KIND,
                        id=key,
                        tenant_id=tenant_id,
                        data={"announcement_id": i, "user_id": user_id},
                    )
                )
                n += 1
        return n

    # roadmap -----------------------------------------------------------------------------------
    async def _put_item(self, it: RoadmapItem) -> RoadmapItem:
        it.updated_at = _now()
        await self.store.put_doc(
            TenantDoc(
                kind=ROADMAP_KIND,
                id=it.id,
                tenant_id=PLATFORM_TENANT,
                data=it.model_dump(mode="json"),
                created_at=it.created_at,
            )
        )
        return it

    async def roadmap(self) -> list[RoadmapItem]:
        docs = await self.store.list_docs(ROADMAP_KIND, PLATFORM_TENANT, 500)
        order = {"in_progress": 0, "planned": 1, "considering": 2, "shipped": 3}
        return sorted(
            (RoadmapItem.model_validate(d.data) for d in docs),
            key=lambda i: (order[i.status], -i.votes, i.created_at),
        )

    async def roadmap_for(self, tenant_id: str) -> list[RoadmapView]:
        voted = {
            d.data.get("item_id")
            for d in await self.store.list_docs(ROADMAP_VOTE_KIND, tenant_id, 2000)
        }
        return [RoadmapView(**i.model_dump(), voted=i.id in voted) for i in await self.roadmap()]

    async def create_item(self, body: RoadmapItemIn) -> RoadmapItem:
        return await self._put_item(RoadmapItem(**body.model_dump()))

    async def update_item(self, item_id: str, body: RoadmapItemIn) -> RoadmapItem:
        doc = await self.store.get_doc(ROADMAP_KIND, item_id)
        if doc is None:
            raise KeyError(item_id)
        return await self._put_item(
            RoadmapItem.model_validate(doc.data).model_copy(update=body.model_dump())
        )

    async def delete_item(self, item_id: str) -> bool:
        return await self.store.delete_doc(ROADMAP_KIND, item_id)

    async def vote(self, tenant_id: str, item_id: str) -> RoadmapView:
        doc = await self.store.get_doc(ROADMAP_KIND, item_id)
        if doc is None:
            raise KeyError(item_id)
        item = RoadmapItem.model_validate(doc.data)
        key = f"{item_id}:{tenant_id}"
        if await self.store.get_doc(ROADMAP_VOTE_KIND, key) is None:
            await self.store.put_doc(
                TenantDoc(
                    kind=ROADMAP_VOTE_KIND, id=key, tenant_id=tenant_id, data={"item_id": item_id}
                )
            )
            item.votes += 1
            await self._put_item(item)
        return RoadmapView(**item.model_dump(), voted=True)

    # feedback ----------------------------------------------------------------------------------
    async def submit_feedback(self, tenant_id: str, author: str, body: FeedbackIn) -> Feedback:
        fb = Feedback(tenant_id=tenant_id, author=author, **body.model_dump())
        await self.store.put_doc(
            TenantDoc(
                kind=FEEDBACK_KIND,
                id=fb.id,
                tenant_id=tenant_id,
                data=fb.model_dump(mode="json"),
                created_at=fb.created_at,
            )
        )
        return fb

    async def feedback(self, tenant_id: str | None = None, limit: int = 500) -> list[Feedback]:
        docs = await self.store.list_docs(FEEDBACK_KIND, tenant_id, limit)
        return sorted(
            (Feedback.model_validate(d.data) for d in docs),
            key=lambda f: f.created_at,
            reverse=True,
        )

    async def set_feedback_status(
        self, fb_id: str, status: Literal["new", "reviewed", "planned", "closed"]
    ) -> Feedback:
        doc = await self.store.get_doc(FEEDBACK_KIND, fb_id)
        if doc is None:
            raise KeyError(fb_id)
        fb = Feedback.model_validate(doc.data)
        fb.status = status
        await self.store.put_doc(
            TenantDoc(
                kind=FEEDBACK_KIND,
                id=fb.id,
                tenant_id=fb.tenant_id,
                data=fb.model_dump(mode="json"),
                created_at=fb.created_at,
            )
        )
        return fb
