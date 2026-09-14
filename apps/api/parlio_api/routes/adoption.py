"""Phase 19b routes — white-glove onboarding, first-week digest, FAQ import, announcements.

Tenant (``/v1``):
* ``GET/POST/DELETE /whiteglove``            eligibility + open request / request / cancel.
* ``GET  /setup/first-week``                 first-week impact report for the Setup page.
* ``POST /assistants/{id}/faqs/import``      text / CSV / URL -> suggested FAQs for review.
* ``POST /assistants/{id}/faqs/apply``       approved FAQs -> new Studio version.
* ``GET  /announcements`` ``POST /announcements/read``  in-app feed + unread count.
* ``GET  /roadmap`` ``POST /roadmap/{id}/vote`` ``POST /feedback``.

Staff (``/v1/admin``): white-glove queue, announcement / roadmap CRUD, feedback triage.
Public (``/v1/public``): ``/changelog`` and ``/roadmap``.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from parlio_api.adoption import (
    AREA_LABELS,
    Announcement,
    AnnouncementFeed,
    AnnouncementIn,
    FaqImportIn,
    FaqImportResult,
    Feedback,
    FeedbackIn,
    FirstWeekReport,
    RoadmapItem,
    RoadmapItemIn,
    RoadmapView,
    WhiteGloveEligibility,
    WhiteGloveRequest,
    WhiteGloveRequestIn,
    WhiteGloveUpdate,
    faqs_from_url,
    first_week_report,
    merge_faqs,
    parse_faq_csv,
    parse_faq_text,
    review_import,
)
from parlio_api.auth import UserDep
from parlio_api.deps import (
    AnnouncementsDep,
    AuditDep,
    BillingDep,
    CalendarDep,
    NotificationsDep,
    SipDep,
    StoreDep,
    ValueDep,
    WhiteGloveDep,
)
from parlio_api.routes.admin import StaffDep, _audit
from parlio_voice.models import AssistantConfig, Faq

router = APIRouter(prefix="/v1", tags=["adoption"])
admin = APIRouter(prefix="/v1/admin", tags=["admin"])
public = APIRouter(prefix="/v1/public", tags=["public"])


# -- white-glove (tenant) ------------------------------------------------------------------------


@router.get("/whiteglove", response_model=WhiteGloveEligibility)
async def whiteglove_status(
    tenant_id: str, user: UserDep, wg: WhiteGloveDep
) -> WhiteGloveEligibility:
    user.require_tenant(tenant_id)
    return await wg.eligibility(tenant_id)


@router.get("/whiteglove/areas", response_model=dict[str, str])
async def whiteglove_areas(user: UserDep) -> dict[str, str]:
    return AREA_LABELS


@router.post("/whiteglove", response_model=WhiteGloveRequest, status_code=201)
async def whiteglove_request(
    tenant_id: str, body: WhiteGloveRequestIn, user: UserDep, wg: WhiteGloveDep
) -> WhiteGloveRequest:
    user.require_tenant(tenant_id)
    try:
        return await wg.request(tenant_id, user.email, body)
    except PermissionError as e:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(e)) from e
    except ValueError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e


@router.delete("/whiteglove/{request_id}", response_model=WhiteGloveRequest)
async def whiteglove_cancel(
    request_id: str, tenant_id: str, user: UserDep, wg: WhiteGloveDep
) -> WhiteGloveRequest:
    user.require_tenant(tenant_id)
    try:
        return await wg.cancel(tenant_id, request_id)
    except KeyError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "request not found") from e


# -- first-week digest ---------------------------------------------------------------------------


@router.get("/setup/first-week", response_model=FirstWeekReport)
async def first_week(
    tenant_id: str,
    user: UserDep,
    store: StoreDep,
    value: ValueDep,
    billing: BillingDep,
    sip: SipDep,
    calendar: CalendarDep,
    notifications: NotificationsDep,
) -> FirstWeekReport:
    user.require_tenant(tenant_id)
    return await first_week_report(tenant_id, store, value, billing, sip, calendar, notifications)


# -- FAQ import ----------------------------------------------------------------------------------


async def _owned_assistant(assistant_id: str, user: UserDep, store: StoreDep) -> AssistantConfig:
    cfg = await store.get_assistant(assistant_id)
    if cfg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "assistant not found")
    user.require_tenant(cfg.tenant_id)
    return cfg


@router.post("/assistants/{assistant_id}/faqs/import", response_model=FaqImportResult)
async def import_faqs(
    assistant_id: str, body: FaqImportIn, user: UserDep, store: StoreDep
) -> FaqImportResult:
    cfg = await _owned_assistant(assistant_id, user, store)
    if body.source == "csv":
        found = parse_faq_csv(body.content, body.category)
    elif body.source == "url":
        try:
            found = await faqs_from_url(body.content.strip())
        except Exception as e:  # network / parse failures surface as a 400 with the reason
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"could not read that page: {e}"
            ) from e
    else:
        found = parse_faq_text(body.content, body.category)
    res = review_import(found, cfg.faqs)
    res.source = body.source
    return res


class FaqApplyIn(BaseModel):
    faqs: list[Faq] = Field(min_length=1, max_length=200)


class FaqApplyResult(BaseModel):
    added: int
    total: int
    config: AssistantConfig


@router.post("/assistants/{assistant_id}/faqs/apply", response_model=FaqApplyResult)
async def apply_faqs(
    assistant_id: str, body: FaqApplyIn, user: UserDep, store: StoreDep
) -> FaqApplyResult:
    cfg = await _owned_assistant(assistant_id, user, store)
    new_cfg, added = merge_faqs(cfg, body.faqs)
    if added:
        await store.upsert_assistant(new_cfg, [])
    return FaqApplyResult(added=added, total=len(new_cfg.faqs), config=new_cfg)


# -- announcements / roadmap / feedback (tenant) -------------------------------------------------


@router.get("/announcements", response_model=AnnouncementFeed)
async def announcements(tenant_id: str, user: UserDep, ann: AnnouncementsDep) -> AnnouncementFeed:
    user.require_tenant(tenant_id)
    return await ann.feed(tenant_id, user.user_id)


class ReadIn(BaseModel):
    announcement_id: str | None = None


@router.post("/announcements/read", response_model=AnnouncementFeed)
async def announcements_read(
    tenant_id: str, body: ReadIn, user: UserDep, ann: AnnouncementsDep
) -> AnnouncementFeed:
    user.require_tenant(tenant_id)
    await ann.mark_read(tenant_id, user.user_id, body.announcement_id)
    return await ann.feed(tenant_id, user.user_id)


@router.get("/roadmap", response_model=list[RoadmapView])
async def roadmap(tenant_id: str, user: UserDep, ann: AnnouncementsDep) -> list[RoadmapView]:
    user.require_tenant(tenant_id)
    return await ann.roadmap_for(tenant_id)


@router.post("/roadmap/{item_id}/vote", response_model=RoadmapView)
async def roadmap_vote(
    item_id: str, tenant_id: str, user: UserDep, ann: AnnouncementsDep
) -> RoadmapView:
    user.require_tenant(tenant_id)
    try:
        return await ann.vote(tenant_id, item_id)
    except KeyError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "roadmap item not found") from e


@router.post("/feedback", response_model=Feedback, status_code=201)
async def feedback(
    tenant_id: str, body: FeedbackIn, user: UserDep, ann: AnnouncementsDep
) -> Feedback:
    user.require_tenant(tenant_id)
    return await ann.submit_feedback(tenant_id, user.email, body)


# -- public --------------------------------------------------------------------------------------


@public.get("/changelog", response_model=list[Announcement])
async def changelog(ann: AnnouncementsDep) -> list[Announcement]:
    return await ann.announcements()


@public.get("/roadmap", response_model=list[RoadmapItem])
async def public_roadmap(ann: AnnouncementsDep) -> list[RoadmapItem]:
    return await ann.roadmap()


# -- staff ---------------------------------------------------------------------------------------


@admin.get("/whiteglove", response_model=list[WhiteGloveRequest])
async def whiteglove_queue(
    user: StaffDep, wg: WhiteGloveDep, status_filter: str | None = None
) -> list[WhiteGloveRequest]:
    rows = await wg.list()
    if status_filter:
        rows = [r for r in rows if r.status == status_filter]
    return rows


@admin.patch("/whiteglove/{request_id}", response_model=WhiteGloveRequest)
async def whiteglove_update(
    request_id: str,
    body: WhiteGloveUpdate,
    request: Request,
    user: StaffDep,
    wg: WhiteGloveDep,
    audit: AuditDep,
) -> WhiteGloveRequest:
    try:
        req = await wg.update(request_id, body, user.email)
    except KeyError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "request not found") from e
    await _audit(
        audit,
        request,
        user,
        req.tenant_id,
        "admin.whiteglove.update",
        request_id,
        {"status": req.status, "assigned_to": req.assigned_to},
    )
    return req


@admin.get("/announcements", response_model=list[Announcement])
async def admin_announcements(user: StaffDep, ann: AnnouncementsDep) -> list[Announcement]:
    return await ann.announcements(include_drafts=True)


@admin.post("/announcements", response_model=Announcement, status_code=201)
async def create_announcement(
    body: AnnouncementIn, user: StaffDep, ann: AnnouncementsDep
) -> Announcement:
    return await ann.create_announcement(body, user.email)


@admin.put("/announcements/{ann_id}", response_model=Announcement)
async def update_announcement(
    ann_id: str, body: AnnouncementIn, user: StaffDep, ann: AnnouncementsDep
) -> Announcement:
    try:
        return await ann.update_announcement(ann_id, body)
    except KeyError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "announcement not found") from e


@admin.delete("/announcements/{ann_id}", status_code=204)
async def delete_announcement(ann_id: str, user: StaffDep, ann: AnnouncementsDep) -> None:
    if not await ann.delete_announcement(ann_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "announcement not found")


@admin.get("/roadmap", response_model=list[RoadmapItem])
async def admin_roadmap(user: StaffDep, ann: AnnouncementsDep) -> list[RoadmapItem]:
    return await ann.roadmap()


@admin.post("/roadmap", response_model=RoadmapItem, status_code=201)
async def create_roadmap_item(
    body: RoadmapItemIn, user: StaffDep, ann: AnnouncementsDep
) -> RoadmapItem:
    return await ann.create_item(body)


@admin.put("/roadmap/{item_id}", response_model=RoadmapItem)
async def update_roadmap_item(
    item_id: str, body: RoadmapItemIn, user: StaffDep, ann: AnnouncementsDep
) -> RoadmapItem:
    try:
        return await ann.update_item(item_id, body)
    except KeyError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "roadmap item not found") from e


@admin.delete("/roadmap/{item_id}", status_code=204)
async def delete_roadmap_item(item_id: str, user: StaffDep, ann: AnnouncementsDep) -> None:
    if not await ann.delete_item(item_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "roadmap item not found")


@admin.get("/feedback", response_model=list[Feedback])
async def admin_feedback(user: StaffDep, ann: AnnouncementsDep) -> list[Feedback]:
    return await ann.feedback()


class FeedbackStatusIn(BaseModel):
    status: Literal["new", "reviewed", "planned", "closed"]


@admin.patch("/feedback/{fb_id}", response_model=Feedback)
async def set_feedback_status(
    fb_id: str, body: FeedbackStatusIn, user: StaffDep, ann: AnnouncementsDep
) -> Feedback:
    try:
        return await ann.set_feedback_status(fb_id, body.status)
    except KeyError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "feedback not found") from e
