"""Phase 22d: the Schedule read model behind the dashboard's day/week view.

One lane per resource (or a single lane for a single-calendar tenant). Each lane carries its
shifts, ParlioTec bookings, other events found on the resource's calendar (busy), travel gaps
between consecutive bookings and a utilisation figure (booked / shift minutes). Results are
cached briefly so the office can refresh without hammering Google/Microsoft.
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from parlio_api.calendar import (
    Booking,
    CalendarConnection,
    CalendarProvider,
    CalendarService,
    Slot,
    _day_window,
)
from parlio_api.resources import BookingMode, outward_code
from parlio_voice.models import Schedule

CACHE_TTL_S = 30


class Block(BaseModel):
    id: str
    kind: str = Field(description="booking | busy | travel")
    start: datetime
    end: datetime
    title: str = ""
    customer: str | None = None
    service: str | None = None
    area: str | None = None
    status: str | None = None
    booking_id: str | None = None
    assignee: str | None = None


class Shift(BaseModel):
    start: datetime
    end: datetime


class Lane(BaseModel):
    resource_id: str | None
    name: str
    role: str = ""
    site_id: str | None = None
    on_call: bool = False
    shifts: list[Shift] = Field(default_factory=list)
    blocks: list[Block] = Field(default_factory=list)
    booked_minutes: int = 0
    shift_minutes: int = 0
    utilisation: float = 0.0
    error: str | None = None


class ScheduleView(BaseModel):
    start: date
    days: int
    timezone: str
    source: str = "calendar"
    read_only: bool = False
    lanes: list[Lane] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    cached: bool = False


def _minutes(a: datetime, b: datetime) -> int:
    return max(0, int((b - a).total_seconds() // 60))


def _booking_block(b: Booking) -> Block:
    return Block(
        id=f"bk:{b.id}",
        kind="booking",
        start=b.start,
        end=b.end,
        title=b.event_title(),
        customer=b.name,
        service=b.service_name,
        area=outward_code(b.address),
        status=b.status,
        booking_id=b.id,
        assignee=b.resource_name,
    )


def _travel_blocks(bookings: list[Booking], buffer: timedelta) -> list[Block]:
    if not buffer:
        return []
    out: list[Block] = []
    ordered = sorted((b for b in bookings if b.status != "cancelled"), key=lambda b: b.start)
    for prev, nxt in pairwise(ordered):
        gap_end = min(prev.end + buffer, nxt.start)
        if gap_end > prev.end:
            out.append(
                Block(
                    id=f"tr:{prev.id}",
                    kind="travel",
                    start=prev.end,
                    end=gap_end,
                    title="Travel",
                )
            )
    return out


def _busy_blocks(busy: list[Slot], bookings: list[Booking], lane: str) -> list[Block]:
    """Calendar events that are not ParlioTec bookings (anything not matching a booking window)."""
    out: list[Block] = []
    for i, s in enumerate(busy):
        if any(b.start == s.start and b.end == s.end for b in bookings):
            continue
        out.append(
            Block(id=f"busy:{lane}:{i}", kind="busy", start=s.start, end=s.end, title="Busy")
        )
    return out


class ScheduleService:
    def __init__(self, calendar: CalendarService) -> None:
        self.calendar = calendar
        self._cache: dict[str, tuple[float, ScheduleView]] = {}

    def invalidate(self, tenant_id: str) -> None:
        for k in [k for k in self._cache if k.startswith(f"{tenant_id}|")]:
            self._cache.pop(k, None)

    async def view(
        self,
        tenant_id: str,
        *,
        start: date,
        days: int = 1,
        site_id: str | None = None,
        service_id: str | None = None,
        resource_id: str | None = None,
        refresh: bool = False,
    ) -> ScheduleView:
        days = 7 if days > 1 else 1
        key = f"{tenant_id}|{start}|{days}|{site_id}|{service_id}|{resource_id}"
        hit = self._cache.get(key)
        if hit and not refresh and time.monotonic() - hit[0] < CACHE_TTL_S:
            return hit[1].model_copy(update={"cached": True})
        view = await self._build(
            tenant_id,
            start=start,
            days=days,
            site_id=site_id,
            service_id=service_id,
            resource_id=resource_id,
        )
        self._cache[key] = (time.monotonic(), view)
        return view

    async def _build(
        self,
        tenant_id: str,
        *,
        start: date,
        days: int,
        site_id: str | None,
        service_id: str | None,
        resource_id: str | None,
    ) -> ScheduleView:
        cal = self.calendar
        primary = await cal.primary(tenant_id)
        hours = await cal.booking_hours(primary) if primary else None
        tz_name = hours.timezone if hours else "Europe/London"
        tz = ZoneInfo(tz_name)
        window_start = datetime.combine(start, datetime.min.time(), tz)
        window_end = window_start + timedelta(days=days)
        settings = await cal.resources.settings(tenant_id)
        sched = await cal._scheduler(tenant_id)
        source = "scheduler" if sched is not None else "calendar"
        read_only = sched is not None and sched.read_only_schedule

        all_bookings = [
            b
            for b in await cal.bookings(tenant_id, 2000)
            if b.start < window_end and b.end > window_start
        ]
        service_name: str | None = None
        if service_id:
            all_bookings = [b for b in all_bookings if b.service_id == service_id]
            if primary is not None:
                svc = next((x for x in primary.rules.services if x.id == service_id), None)
                service_name = svc.name if svc else None

        team = await cal.resources.all(tenant_id, include_inactive=False)
        if service_id:
            team = [r for r in team if r.can_do(service_id, service_name)]
        if site_id:
            team = [r for r in team if r.site_id in (None, site_id)]
        if resource_id:
            team = [r for r in team if r.id == resource_id]

        view = ScheduleView(
            start=start, days=days, timezone=tz_name, source=source, read_only=read_only
        )
        buffer = timedelta(minutes=primary.buffer_minutes) if primary else timedelta(0)

        if not team:
            lane = Lane(
                resource_id=None,
                name=primary.name if primary else "Bookings",
                role="Calendar",
            )
            lane_bookings = [
                b for b in all_bookings if not resource_id or b.resource_id == resource_id
            ]
            busy: list[Slot] = []
            if sched is not None and cal.scheduler is not None:
                try:
                    ext = await cal.scheduler.busy(sched, window_start, window_end)
                    busy = [Slot(start=s.start, end=s.end) for s in ext]
                except Exception as e:
                    lane.error = str(e)[:200]
            elif primary is not None and primary.bookable:
                be = cal.backends.get(primary.provider)
                if be is not None and primary.provider != CalendarProvider.BOOKING_LINK:
                    try:
                        busy = await be.busy(primary, window_start, window_end)
                    except Exception as e:
                        lane.error = str(e)[:200]
            self._fill(lane, hours, start, days, tz, lane_bookings, busy, buffer)
            view.lanes.append(lane)
            return view

        cache: dict[str, CalendarConnection | None] = {}
        ext_busy_by_ref: dict[str, list[Slot]] = {}
        if sched is not None and cal.scheduler is not None:
            try:
                for s in await cal.scheduler.busy(sched, window_start, window_end):
                    ext_busy_by_ref.setdefault(s.resource_id or "", []).append(
                        Slot(start=s.start, end=s.end)
                    )
            except Exception:
                ext_busy_by_ref = {}
        for r in team:
            lane = Lane(
                resource_id=r.id, name=r.name, role=r.role, site_id=r.site_id, on_call=r.on_call
            )
            mine = [b for b in all_bookings if b.resource_id == r.id]
            busy = []
            if sched is not None:
                busy = ext_busy_by_ref.get(r.external_ref or "", [])
            elif primary is not None:
                rconn = await cal._conn_for(r, primary, cache)
                be = cal.backends.get(rconn.provider) if rconn else None
                if rconn is not None and be is not None:
                    try:
                        busy = await be.busy(rconn, window_start, window_end)
                    except Exception as e:
                        lane.error = str(e)[:200]
            self._fill(lane, r.hours or hours, start, days, tz, mine, busy, buffer)
            view.lanes.append(lane)
        unassigned = [b for b in all_bookings if not b.resource_id and not resource_id]
        if unassigned and settings.mode == BookingMode.CALENDAR:
            lane = Lane(resource_id=None, name="Unassigned", role="")
            self._fill(lane, hours, start, days, tz, unassigned, [], buffer)
            view.lanes.append(lane)
        return view

    @staticmethod
    def _fill(
        lane: Lane,
        hours: Schedule | None,
        start: date,
        days: int,
        tz: ZoneInfo,
        bookings: list[Booking],
        busy: list[Slot],
        buffer: timedelta,
    ) -> None:
        for i in range(days):
            day = start + timedelta(days=i)
            if hours is None:
                continue
            w = _day_window(hours, day, tz)
            if w is not None:
                lane.shifts.append(Shift(start=w[0], end=w[1]))
                lane.shift_minutes += _minutes(w[0], w[1])
        live = [b for b in bookings if b.status != "cancelled"]
        lane.blocks = (
            [_booking_block(b) for b in bookings]
            + _travel_blocks(live, buffer)
            + _busy_blocks(busy, bookings, lane.resource_id or "cal")
        )
        lane.blocks.sort(key=lambda b: b.start)
        lane.booked_minutes = sum(_minutes(b.start, b.end) for b in live)
        lane.utilisation = (
            round(lane.booked_minutes / lane.shift_minutes, 3) if lane.shift_minutes else 0.0
        )
