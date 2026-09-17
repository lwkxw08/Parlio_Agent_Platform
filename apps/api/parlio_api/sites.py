"""Phase 20g: multi-location / multi-brand.

Sites live on the assistant config (`AssistantConfig.sites`, shared with the voice worker),
so the same list that picks the greeting/brand on a call also drives dashboard filters and the
franchise roll-up. A call belongs to a site when the number it came in on is one of that site's
numbers; historical calls are attributed retrospectively, so no per-call column is needed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field

from parlio_api.store import CallRecord, CallStore
from parlio_voice.models import AssistantConfig, SiteRef


def _digits(number: str | None) -> str:
    return (number or "").lstrip("+")


class Site(SiteRef):
    """A `SiteRef` plus which assistant it belongs to (an organisation may run several)."""

    assistant_id: str
    assistant_name: str = ""
    departments: list[str] = Field(default_factory=list)
    destinations: int = 0


async def sites_for_tenant(store: CallStore, tenant_id: str) -> list[Site]:
    out: list[Site] = []
    for cfg in await store.list_assistants(tenant_id):
        out.extend(_sites_of(cfg))
    return out


def _sites_of(cfg: AssistantConfig) -> list[Site]:
    return [
        Site(
            **s.model_dump(),
            assistant_id=cfg.assistant_id,
            assistant_name=cfg.name,
            departments=sorted(
                {d.department for d in cfg.transfer.destinations if d.site_id in (None, s.id)}
            ),
            destinations=sum(1 for d in cfg.transfer.destinations if d.site_id == s.id),
        )
        for s in cfg.sites
    ]


def site_of(call: CallRecord, sites: list[Site]) -> Site | None:
    """The site a call belongs to (inbound number match); `None` = head office / unassigned."""
    if call.direction == "outbound":
        return None
    return next((s for s in sites if s.owns(call.dialed)), None)


def numbers_for_site(sites: list[Site], site_id: str) -> list[str] | None:
    """Numbers to filter calls on for one site; `None` when the site is unknown."""
    site = next((s for s in sites if s.id == site_id), None)
    return [_digits(n) for n in site.numbers] if site else None


def unassigned_numbers(calls: list[CallRecord], sites: list[Site]) -> list[str]:
    """Inbound numbers that received calls but belong to no site (so the owner can assign them)."""
    seen: dict[str, None] = {}
    for c in calls:
        if c.direction != "outbound" and c.dialed and site_of(c, sites) is None:
            seen.setdefault(c.dialed, None)
    return list(seen)


class SiteSummary(BaseModel):
    site_id: str | None  # None = calls on numbers not assigned to any site
    name: str
    brand_name: str = ""
    numbers: list[str] = Field(default_factory=list)
    calls: int = 0
    answered: int = 0
    missed: int = 0
    transferred: int = 0
    ticketed: int = 0
    blocked: int = 0
    after_hours: int = 0
    avg_duration_s: float | None = None
    answer_rate: float | None = None  # % of non-blocked calls that were answered by someone
    share_pct: float = 0.0  # share of the organisation's calls


class SiteRollup(BaseModel):
    days: int
    since: datetime
    total_calls: int
    sites: list[SiteSummary]
    unassigned_numbers: list[str] = Field(default_factory=list)
    best_answer_rate: str | None = None
    most_missed: str | None = None


def rollup(
    calls: list[CallRecord],
    sites: list[Site],
    cfgs: list[AssistantConfig],
    *,
    days: int = 30,
    now: datetime | None = None,
) -> SiteRollup:
    """Franchise / multi-branch view: every site side by side over the window."""
    now = now or datetime.now(UTC)
    since = now - timedelta(days=days)
    window = [c for c in calls if c.started_at >= since and c.direction != "outbound"]
    hours_by_assistant = {c.assistant_id: c.hours for c in cfgs}

    rows: dict[str | None, SiteSummary] = {
        s.id: SiteSummary(
            site_id=s.id, name=s.name, brand_name=s.brand_name, numbers=list(s.numbers)
        )
        for s in sites
    }
    durations: dict[str | None, list[float]] = {k: [] for k in rows}
    durations[None] = []
    for c in window:
        s = site_of(c, sites)
        key = s.id if s else None
        if key not in rows:
            rows[key] = SiteSummary(site_id=None, name="Unassigned numbers")
        r = rows[key]
        r.calls += 1
        match c.kind:
            case "missed":
                r.missed += 1
            case "transferred":
                r.transferred += 1
                r.answered += 1
            case "ticketed":
                r.ticketed += 1
                r.answered += 1
            case "blocked":
                r.blocked += 1
            case "answered":
                r.answered += 1
        if c.duration_s:
            durations[key].append(c.duration_s)
        hours = hours_by_assistant.get(c.assistant_id)
        if hours is not None and not hours.is_open(c.started_at):
            r.after_hours += 1

    total = sum(r.calls for r in rows.values())
    for key, r in rows.items():
        d = durations.get(key) or []
        r.avg_duration_s = round(sum(d) / len(d), 1) if d else None
        eligible = r.calls - r.blocked
        r.answer_rate = round(r.answered / eligible * 100, 1) if eligible else None
        r.share_pct = round(r.calls / total * 100, 1) if total else 0.0

    ordered = sorted(rows.values(), key=lambda r: (r.site_id is None, -r.calls, r.name))
    rated = [r for r in ordered if r.site_id and r.answer_rate is not None and r.calls >= 5]
    return SiteRollup(
        days=days,
        since=since,
        total_calls=total,
        sites=ordered,
        unassigned_numbers=unassigned_numbers(window, sites),
        best_answer_rate=max(rated, key=lambda r: r.answer_rate or 0).name if rated else None,
        most_missed=max(rated, key=lambda r: r.missed).name
        if any(r.missed for r in rated)
        else None,
    )
