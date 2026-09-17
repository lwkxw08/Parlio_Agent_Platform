"""In-app user guide + "Ask Parlio" help assistant (``/v1/help``)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from parlio_api.auth import UserDep
from parlio_api.deps import HelpDep
from parlio_api.help import GuidePage, GuidePageSummary, HelpAnswer, HelpQuestion

router = APIRouter(prefix="/v1/help", tags=["help"])


@router.get("/pages", response_model=list[GuidePageSummary])
async def help_pages(user: UserDep, help: HelpDep) -> list[GuidePageSummary]:
    return help.guide.summaries()


@router.get("/pages/{slug}", response_model=GuidePage)
async def help_page(slug: str, user: UserDep, help: HelpDep) -> GuidePage:
    page = help.guide.page(slug)
    if page is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No guide page with that name")
    return page


@router.get("/for-route", response_model=GuidePage | None)
async def help_for_route(route: str, user: UserDep, help: HelpDep) -> GuidePage | None:
    return help.guide.page_for_route(route)


@router.post("/ask", response_model=HelpAnswer)
async def help_ask(body: HelpQuestion, user: UserDep, help: HelpDep) -> HelpAnswer:
    return await help.ask(body)
