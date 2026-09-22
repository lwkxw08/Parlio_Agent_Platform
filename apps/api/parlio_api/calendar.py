"""Calendar & booking integration: mid-call availability checks and appointment booking.

Two shapes of integration share one `CalendarConnection` model:
- OAuth calendars (Google, Microsoft/Outlook): ParlioTec reads free/busy and creates events. The
  refresh token is sealed in the vault; only `has_token` is ever exposed.
- Booking links (Cal.com, Square, GoHighLevel, anything with a URL): no API access - the assistant
  texts the link (via the `booking_link` SMS scenario) instead of booking directly.

`CalendarBackend` is the provider seam; `SimulatedBackend` makes the whole flow testable offline.
Every provider call is appended to the sync log so the dashboard can show what happened.
"""

from __future__ import annotations

import logging
import secrets
from collections import Counter
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from typing import Any, Protocol
from urllib.parse import urlencode
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel, Field

from parlio_api.resources import (
    AssignmentPolicy,
    BookingMode,
    Resource,
    ResourceService,
    choose,
    day_key,
    eligible,
    outward_code,
)
from parlio_api.scheduling import ExternalSlot, SchedulerConfig, SchedulingService
from parlio_api.store import CallStore, TenantDoc
from parlio_api.vault import Vault
from parlio_voice.models import WEEKDAYS, Schedule

log = logging.getLogger("parlio.api.calendar")

CONN_KIND = "calendar_connection"
BOOKING_KIND = "booking"
SYNC_KIND = "calendar_sync"
STATE_KIND = "oauth_state"


class CalendarProvider(StrEnum):
    GOOGLE = "google"
    MICROSOFT = "microsoft"
    BOOKING_LINK = "booking_link"
    SIMULATED = "simulated"


class ConnectionStatus(StrEnum):
    PENDING = "pending"
    CONNECTED = "connected"
    ERROR = "error"


class Slot(BaseModel):
    start: datetime
    end: datetime

    def overlaps(self, other: Slot) -> bool:
        return self.start < other.end and other.start < self.end


class ServiceType(BaseModel):
    """A bookable service the tenant offers, with its own appointment length."""

    id: str = Field(default_factory=lambda: f"svc-{uuid4().hex[:6]}")
    name: str = Field(min_length=1, max_length=80)
    minutes: int = Field(default=60, ge=5, le=480)
    description: str | None = Field(default=None, max_length=300)
    emergency: bool = Field(
        default=False,
        description="Emergency service: may be booked outside booking hours and off the grid.",
    )


class BookingRules(BaseModel):
    """Tenant-set rules the assistant follows when offering and booking slots."""

    align_minutes: int = Field(
        default=30,
        ge=0,
        le=120,
        description="Start times fall on this grid (60 = on the hour, 30 = hour/half-past; "
        "0 = back to back).",
    )
    use_business_hours: bool = Field(
        default=True, description="Book within the assistant's business hours (else own hours)."
    )
    min_notice_minutes: int = Field(default=0, ge=0, le=7 * 24 * 60)
    max_days_ahead: int = Field(default=30, ge=1, le=365)
    emergency_any_time: bool = Field(
        default=True, description="Emergency services ignore hours, grid and notice."
    )
    services: list[ServiceType] = Field(default_factory=list)

    def service(self, service_id: str | None) -> ServiceType | None:
        if not service_id:
            return None
        key = service_id.strip().lower()
        return next((s for s in self.services if s.id == service_id or s.name.lower() == key), None)


class CalendarConnection(BaseModel):
    id: str = Field(default_factory=lambda: f"cal-{uuid4().hex[:8]}")
    tenant_id: str
    company_id: str
    provider: CalendarProvider
    name: str = "Bookings"
    status: ConnectionStatus = ConnectionStatus.PENDING
    calendar_id: str = "primary"
    account_email: str | None = None
    booking_url: str | None = None
    booking_vendor: str | None = Field(default=None, description="cal.com | square | ghl | other")
    slot_minutes: int = Field(default=30, ge=5, le=480)
    buffer_minutes: int = Field(default=0, ge=0, le=120)
    hours: Schedule = Field(default_factory=Schedule)
    rules: BookingRules = Field(default_factory=BookingRules)
    token_sealed: str | None = Field(default=None, exclude=True)
    last_sync_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def has_token(self) -> bool:
        return bool(self.token_sealed)

    @property
    def bookable(self) -> bool:
        return self.provider != CalendarProvider.BOOKING_LINK and (
            self.provider == CalendarProvider.SIMULATED or self.has_token
        )

    def public(self) -> dict[str, Any]:
        return {
            **self.model_dump(mode="json"),
            "has_token": self.has_token,
            "bookable": self.bookable,
        }

    def to_doc(self) -> TenantDoc:
        data = self.model_dump(mode="json")
        data["token_sealed"] = self.token_sealed
        return TenantDoc(
            kind=CONN_KIND,
            id=self.id,
            tenant_id=self.tenant_id,
            data=data,
            created_at=self.created_at,
        )

    @classmethod
    def from_doc(cls, d: TenantDoc) -> CalendarConnection:
        return cls.model_validate(d.data)


class Booking(BaseModel):
    id: str = Field(default_factory=lambda: f"bk-{uuid4().hex[:8]}")
    tenant_id: str
    company_id: str
    connection_id: str
    call_id: str | None = None
    start: datetime
    end: datetime
    name: str
    phone: str | None = None
    notes: str | None = None
    address: str | None = None
    caller_id: str | None = None
    call_link: str | None = None
    service_id: str | None = None
    service_name: str | None = None
    provider_ref: str | None = None
    status: str = "confirmed"
    resource_id: str | None = None
    resource_name: str | None = None
    source: str = Field(default="calendar", description="calendar | scheduler")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime | None = None

    @property
    def postcode_area(self) -> str | None:
        return outward_code(self.address)

    def event_title(self) -> str:
        return f"{self.service_name} - {self.name}" if self.service_name else self.name

    def event_description(self) -> str:
        """Everything the person doing the job needs, as plain text for the calendar event."""
        rows: list[tuple[str, str | None]] = [
            ("Service", self.service_name),
            ("Customer", self.name),
            ("Phone", self.phone),
            ("Address", self.address),
            ("Details", self.notes),
        ]
        if self.caller_id and self.caller_id != self.phone:
            rows.append(("Called from", self.caller_id))
        if self.resource_name:
            rows.append(("Assigned to", self.resource_name))
        lines = [f"{k}: {v}" for k, v in rows if v]
        lines.append("")
        lines.append(
            f"Booked by ParlioTec from a call. Recording and transcript: {self.call_link}"
            if self.call_link
            else "Booked by ParlioTec."
        )
        return "\n".join(lines)

    def to_doc(self) -> TenantDoc:
        return TenantDoc(
            kind=BOOKING_KIND,
            id=self.id,
            tenant_id=self.tenant_id,
            data=self.model_dump(mode="json"),
            created_at=self.created_at,
        )


class SyncLogEntry(BaseModel):
    id: str = Field(default_factory=lambda: f"cs-{uuid4().hex[:10]}")
    tenant_id: str
    connection_id: str
    action: str
    ok: bool = True
    detail: str | None = None
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def to_doc(self) -> TenantDoc:
        return TenantDoc(
            kind=SYNC_KIND,
            id=self.id,
            tenant_id=self.tenant_id,
            data=self.model_dump(mode="json"),
            created_at=self.at,
        )


class BookingRequest(BaseModel):
    connection_id: str | None = None
    start: datetime
    name: str = Field(min_length=1)
    phone: str | None = None
    notes: str | None = None
    address: str | None = None
    caller_id: str | None = None
    call_id: str | None = None
    duration_minutes: int | None = None
    service_id: str | None = None


class OAuthTokens(BaseModel):
    refresh_token: str
    account_email: str | None = None


# -- free slot computation -------------------------------------------------------------------


def _day_window(hours: Schedule, day: date, tz: ZoneInfo) -> tuple[datetime, datetime] | None:
    """Open/close for `day` under `hours`, honouring holidays; None when closed."""
    if hours.always:
        return datetime.combine(day, time(0, 0), tz), datetime.combine(day, time(23, 59), tz)
    dh = hours.hours.get(WEEKDAYS[day.weekday()])
    hol = next((h for h in hours.holidays if h.day == day), None)
    if hol is not None:
        dh = None if hol.closed else (hol.hours or dh)
    if dh is None or dh.open >= dh.close:
        return None
    return datetime.combine(day, dh.open, tz), datetime.combine(day, dh.close, tz)


def _align(t: datetime, grid: timedelta) -> datetime:
    """Round `t` up to the next point on the grid (measured from local midnight)."""
    if not grid:
        return t
    midnight = t.replace(hour=0, minute=0, second=0, microsecond=0)
    over = (t - midnight) % grid
    return t if not over else t + (grid - over)


def slot_length(
    conn: CalendarConnection, duration_minutes: int | None, service: ServiceType | None
) -> timedelta:
    if service is not None:
        return timedelta(minutes=service.minutes)
    return timedelta(minutes=duration_minutes or conn.slot_minutes)


def free_slots(
    conn: CalendarConnection,
    busy: list[Slot],
    start: datetime,
    end: datetime,
    *,
    now: datetime | None = None,
    duration_minutes: int | None = None,
    limit: int = 20,
    hours: Schedule | None = None,
    service: ServiceType | None = None,
) -> list[Slot]:
    """Slots the booking rules allow between start/end: inside the booking hours (finishing by
    close), on the start-time grid, after the notice period, clear of busy periods plus the gap
    either side. Emergency services (when allowed) ignore hours, grid and notice."""
    now = now or datetime.now(UTC)
    rules = conn.rules
    hours = hours or conn.hours
    tz = ZoneInfo(hours.timezone)
    length = slot_length(conn, duration_minutes, service)
    any_time = bool(service and service.emergency and rules.emergency_any_time)
    grid = timedelta(minutes=0 if any_time else rules.align_minutes)
    step = grid or timedelta(minutes=15 if any_time else conn.slot_minutes)
    pad = timedelta(minutes=conn.buffer_minutes)
    earliest = max(start, now if any_time else now + timedelta(minutes=rules.min_notice_minutes))
    end = min(end, now + timedelta(days=rules.max_days_ahead))
    out: list[Slot] = []
    day = start.astimezone(tz).date()
    last = end.astimezone(tz).date()
    while day <= last and len(out) < limit:
        window = (
            _day_window(Schedule(timezone=hours.timezone, always=True), day, tz)
            if any_time
            else _day_window(hours, day, tz)
        )
        if window is None:
            day += timedelta(days=1)
            continue
        open_at, close = window
        cursor = _align(max(open_at, earliest.astimezone(tz)), grid)
        while cursor + length <= close and len(out) < limit:
            cand = Slot(start=cursor, end=cursor + length)
            padded = Slot(start=cursor - pad, end=cursor + length + pad)
            fits = cand.start >= earliest and cand.end <= end
            if fits and not any(padded.overlaps(b) for b in busy):
                out.append(cand)
            cursor += step
        day += timedelta(days=1)
    return out


def slot_allowed(
    conn: CalendarConnection,
    start: datetime,
    *,
    now: datetime,
    hours: Schedule | None = None,
    duration_minutes: int | None = None,
    service: ServiceType | None = None,
) -> str | None:
    """Reason a booking at `start` breaks the rules (ignoring busy periods), else None."""
    rules = conn.rules
    hours = hours or conn.hours
    if service is not None and service.emergency and rules.emergency_any_time:
        return None if start >= now - timedelta(minutes=5) else "that time has passed"
    tz = ZoneInfo(hours.timezone)
    local = start.astimezone(tz)
    length = slot_length(conn, duration_minutes, service)
    if start < now + timedelta(minutes=rules.min_notice_minutes):
        return f"bookings need at least {rules.min_notice_minutes} minutes' notice"
    if start > now + timedelta(days=rules.max_days_ahead):
        return f"bookings can be made up to {rules.max_days_ahead} days ahead"
    window = _day_window(hours, local.date(), tz)
    if window is None:
        return "we are closed that day"
    open_at, close = window
    if local < open_at or local + length > close:
        return "appointments must start and finish within booking hours"
    if _align(local, timedelta(minutes=rules.align_minutes)) != local:
        return f"start times must be on the {rules.align_minutes}-minute grid"
    return None


# -- provider backends -----------------------------------------------------------------------


class CalendarBackend(Protocol):
    provider: CalendarProvider

    async def busy(
        self, conn: CalendarConnection, start: datetime, end: datetime
    ) -> list[Slot]: ...
    async def create_event(self, conn: CalendarConnection, booking: Booking) -> str: ...
    async def delete_event(self, conn: CalendarConnection, provider_ref: str) -> None: ...


class OAuthBackend(CalendarBackend, Protocol):
    def auth_url(self, state: str, redirect_uri: str) -> str: ...
    async def exchange_code(self, code: str, redirect_uri: str) -> OAuthTokens: ...


class SimulatedBackend:
    """In-memory diary; one independent set of busy slots per calendar id so a shared
    connection with one calendar per engineer behaves like the real providers."""

    provider = CalendarProvider.SIMULATED

    def __init__(self, busy: list[Slot] | None = None) -> None:
        self.busy_slots = busy or []
        self._per_calendar: dict[str, list[Slot]] = {}
        self.events: list[Booking] = []
        self.fail = False

    def _diary(self, conn: CalendarConnection) -> list[Slot]:
        cid = conn.calendar_id or "primary"
        if cid == "primary":
            return self.busy_slots
        return self._per_calendar.setdefault(cid, [])

    async def busy(self, conn: CalendarConnection, start: datetime, end: datetime) -> list[Slot]:
        if self.fail:
            raise RuntimeError("simulated calendar outage")
        return [b for b in self._diary(conn) if b.overlaps(Slot(start=start, end=end))]

    async def create_event(self, conn: CalendarConnection, booking: Booking) -> str:
        if self.fail:
            raise RuntimeError("simulated calendar outage")
        self.events.append(booking)
        self._diary(conn).append(Slot(start=booking.start, end=booking.end))
        return f"sim-{len(self.events)}"

    async def delete_event(self, conn: CalendarConnection, provider_ref: str) -> None:
        if self.fail:
            raise RuntimeError("simulated calendar outage")
        ev = next((b for b in self.events if b.provider_ref == provider_ref), None)
        if ev is not None:
            self.events.remove(ev)
            slot = Slot(start=ev.start, end=ev.end)
            diary = self._diary(conn)
            if slot in diary:
                diary.remove(slot)


class GoogleCalendarBackend:
    provider = CalendarProvider.GOOGLE
    AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN = "https://oauth2.googleapis.com/token"
    API = "https://www.googleapis.com/calendar/v3"
    SCOPES = (
        "https://www.googleapis.com/auth/calendar.readonly "
        "https://www.googleapis.com/auth/calendar.events openid email"
    )

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        vault: Vault,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._id, self._secret, self._vault = client_id, client_secret, vault
        self._http = http or httpx.AsyncClient(timeout=15)

    def auth_url(self, state: str, redirect_uri: str) -> str:
        q = {
            "client_id": self._id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": self.SCOPES,
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
        return f"{self.AUTH}?{urlencode(q)}"

    async def exchange_code(self, code: str, redirect_uri: str) -> OAuthTokens:
        r = await self._http.post(
            self.TOKEN,
            data={
                "code": code,
                "client_id": self._id,
                "client_secret": self._secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        r.raise_for_status()
        tok = r.json()
        email = None
        if tok.get("access_token"):
            u = await self._http.get(
                "https://openidconnect.googleapis.com/v1/userinfo",
                headers={"Authorization": f"Bearer {tok['access_token']}"},
            )
            if u.is_success:
                email = u.json().get("email")
        return OAuthTokens(refresh_token=tok["refresh_token"], account_email=email)

    async def _access_token(self, conn: CalendarConnection) -> str:
        if not conn.token_sealed:
            raise RuntimeError("calendar not connected")
        r = await self._http.post(
            self.TOKEN,
            data={
                "refresh_token": self._vault.open(conn.token_sealed),
                "client_id": self._id,
                "client_secret": self._secret,
                "grant_type": "refresh_token",
            },
        )
        r.raise_for_status()
        return str(r.json()["access_token"])

    async def busy(self, conn: CalendarConnection, start: datetime, end: datetime) -> list[Slot]:
        tok = await self._access_token(conn)
        r = await self._http.post(
            f"{self.API}/freeBusy",
            headers={"Authorization": f"Bearer {tok}"},
            json={
                "timeMin": start.isoformat(),
                "timeMax": end.isoformat(),
                "items": [{"id": conn.calendar_id}],
            },
        )
        r.raise_for_status()
        periods = r.json()["calendars"].get(conn.calendar_id, {}).get("busy", [])
        return [
            Slot(start=datetime.fromisoformat(p["start"]), end=datetime.fromisoformat(p["end"]))
            for p in periods
        ]

    async def create_event(self, conn: CalendarConnection, booking: Booking) -> str:
        tok = await self._access_token(conn)
        r = await self._http.post(
            f"{self.API}/calendars/{conn.calendar_id}/events",
            headers={"Authorization": f"Bearer {tok}"},
            json={
                "summary": booking.event_title(),
                "description": booking.event_description(),
                **({"location": booking.address} if booking.address else {}),
                "start": {"dateTime": booking.start.isoformat()},
                "end": {"dateTime": booking.end.isoformat()},
            },
        )
        r.raise_for_status()
        return str(r.json()["id"])

    async def delete_event(self, conn: CalendarConnection, provider_ref: str) -> None:
        tok = await self._access_token(conn)
        r = await self._http.delete(
            f"{self.API}/calendars/{conn.calendar_id}/events/{provider_ref}",
            headers={"Authorization": f"Bearer {tok}"},
        )
        if r.status_code not in (404, 410):
            r.raise_for_status()


class MicrosoftCalendarBackend:
    provider = CalendarProvider.MICROSOFT
    AUTH = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
    TOKEN = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
    GRAPH = "https://graph.microsoft.com/v1.0"
    SCOPES = "offline_access User.Read Calendars.ReadWrite"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        vault: Vault,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._id, self._secret, self._vault = client_id, client_secret, vault
        self._http = http or httpx.AsyncClient(timeout=15)

    def auth_url(self, state: str, redirect_uri: str) -> str:
        q = {
            "client_id": self._id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "response_mode": "query",
            "scope": self.SCOPES,
            "state": state,
        }
        return f"{self.AUTH}?{urlencode(q)}"

    async def exchange_code(self, code: str, redirect_uri: str) -> OAuthTokens:
        r = await self._http.post(
            self.TOKEN,
            data={
                "code": code,
                "client_id": self._id,
                "client_secret": self._secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
                "scope": self.SCOPES,
            },
        )
        r.raise_for_status()
        tok = r.json()
        email = None
        me = await self._http.get(
            f"{self.GRAPH}/me", headers={"Authorization": f"Bearer {tok['access_token']}"}
        )
        if me.is_success:
            j = me.json()
            email = j.get("mail") or j.get("userPrincipalName")
        return OAuthTokens(refresh_token=tok["refresh_token"], account_email=email)

    async def _access_token(self, conn: CalendarConnection) -> str:
        if not conn.token_sealed:
            raise RuntimeError("calendar not connected")
        r = await self._http.post(
            self.TOKEN,
            data={
                "refresh_token": self._vault.open(conn.token_sealed),
                "client_id": self._id,
                "client_secret": self._secret,
                "grant_type": "refresh_token",
                "scope": self.SCOPES,
            },
        )
        r.raise_for_status()
        return str(r.json()["access_token"])

    def _cal(self, conn: CalendarConnection) -> str:
        return (
            f"{self.GRAPH}/me"
            if conn.calendar_id in ("primary", "", None)
            else f"{self.GRAPH}/me/calendars/{conn.calendar_id}"
        )

    async def busy(self, conn: CalendarConnection, start: datetime, end: datetime) -> list[Slot]:
        tok = await self._access_token(conn)
        r = await self._http.get(
            f"{self._cal(conn)}/calendarView",
            headers={"Authorization": f"Bearer {tok}", "Prefer": 'outlook.timezone="UTC"'},
            params={
                "startDateTime": start.astimezone(UTC).isoformat(),
                "endDateTime": end.astimezone(UTC).isoformat(),
                "$select": "start,end,showAs",
                "$top": "200",
            },
        )
        r.raise_for_status()
        out: list[Slot] = []
        for ev in r.json().get("value", []):
            if ev.get("showAs") == "free":
                continue
            out.append(
                Slot(
                    start=_graph_dt(ev["start"]["dateTime"]),
                    end=_graph_dt(ev["end"]["dateTime"]),
                )
            )
        return out

    async def create_event(self, conn: CalendarConnection, booking: Booking) -> str:
        tok = await self._access_token(conn)
        r = await self._http.post(
            f"{self._cal(conn)}/events",
            headers={"Authorization": f"Bearer {tok}"},
            json={
                "subject": booking.event_title(),
                "body": {"contentType": "text", "content": booking.event_description()},
                **({"location": {"displayName": booking.address}} if booking.address else {}),
                "start": {"dateTime": booking.start.astimezone(UTC).isoformat(), "timeZone": "UTC"},
                "end": {"dateTime": booking.end.astimezone(UTC).isoformat(), "timeZone": "UTC"},
            },
        )
        r.raise_for_status()
        return str(r.json()["id"])

    async def delete_event(self, conn: CalendarConnection, provider_ref: str) -> None:
        tok = await self._access_token(conn)
        r = await self._http.delete(
            f"{self.GRAPH}/me/events/{provider_ref}",
            headers={"Authorization": f"Bearer {tok}"},
        )
        if r.status_code != 404:
            r.raise_for_status()


def _graph_dt(s: str) -> datetime:
    # Graph returns 7 fractional digits without offset when Prefer: outlook.timezone="UTC".
    return datetime.fromisoformat(s[:26]).replace(tzinfo=UTC)


# -- service ---------------------------------------------------------------------------------


class AvailabilityResult(BaseModel):
    connection_id: str | None
    provider: CalendarProvider | None
    slots: list[Slot] = Field(default_factory=list)
    booking_url: str | None = None
    error: str | None = None
    services: list[ServiceType] = Field(default_factory=list)
    service_id: str | None = None
    slot_minutes: int | None = None
    source: str = "calendar"
    resources: int = Field(default=0, description="Resources pooled into these slots")


class Candidate(BaseModel):
    """A resource that can take a slot, with the calendar it books into."""

    resource: Resource
    conn: CalendarConnection


def external_slots(slots: list[ExternalSlot]) -> list[Slot]:
    seen: set[datetime] = set()
    out: list[Slot] = []
    for s in sorted(slots, key=lambda x: x.start):
        if s.start in seen:
            continue
        seen.add(s.start)
        out.append(Slot(start=s.start, end=s.end))
    return out


class CalendarService:
    def __init__(
        self,
        store: CallStore,
        vault: Vault,
        backends: dict[CalendarProvider, CalendarBackend] | None = None,
        dashboard_url: str = "",
        resources: ResourceService | None = None,
        scheduler: SchedulingService | None = None,
    ) -> None:
        self.store = store
        self.vault = vault
        self.backends = backends or {}
        self.dashboard_url = dashboard_url.rstrip("/")
        self.resources = resources or ResourceService(store)
        self.scheduler = scheduler
        self.on_booked: Callable[[Booking], Awaitable[None]] | None = None
        self.on_changed: Callable[[Booking, str], Awaitable[None]] | None = None

    # connections
    async def connections(self, tenant_id: str) -> list[CalendarConnection]:
        docs = await self.store.list_docs(CONN_KIND, tenant_id)
        return sorted((CalendarConnection.from_doc(d) for d in docs), key=lambda c: c.created_at)

    async def get(self, tenant_id: str, conn_id: str) -> CalendarConnection | None:
        d = await self.store.get_doc(CONN_KIND, conn_id)
        if d is None or d.tenant_id != tenant_id:
            return None
        return CalendarConnection.from_doc(d)

    async def primary(self, tenant_id: str) -> CalendarConnection | None:
        conns = await self.connections(tenant_id)
        bookable = [c for c in conns if c.bookable and c.status == ConnectionStatus.CONNECTED]
        return bookable[0] if bookable else (conns[0] if conns else None)

    async def booking_hours(self, conn: CalendarConnection) -> Schedule:
        """Hours bookings must sit inside: the tenant's assistant hours or the connection's own."""
        if not conn.rules.use_business_hours:
            return conn.hours
        assistants = await self.store.list_assistants(conn.tenant_id)
        return assistants[0].hours if assistants else conn.hours

    async def put(self, conn: CalendarConnection) -> CalendarConnection:
        if conn.provider == CalendarProvider.BOOKING_LINK:
            conn.status = (
                ConnectionStatus.CONNECTED if conn.booking_url else ConnectionStatus.PENDING
            )
        elif conn.provider == CalendarProvider.SIMULATED:
            conn.status = ConnectionStatus.CONNECTED
        await self.store.put_doc(conn.to_doc())
        return conn

    async def delete(self, tenant_id: str, conn_id: str) -> bool:
        if await self.get(tenant_id, conn_id) is None:
            return False
        return await self.store.delete_doc(CONN_KIND, conn_id)

    async def sync_log(self, tenant_id: str, limit: int = 100) -> list[SyncLogEntry]:
        docs = await self.store.list_docs(SYNC_KIND, tenant_id, limit)
        return [SyncLogEntry.model_validate(d.data) for d in docs]

    async def bookings(self, tenant_id: str, limit: int = 100) -> list[Booking]:
        docs = await self.store.list_docs(BOOKING_KIND, tenant_id, limit)
        return [Booking.model_validate(d.data) for d in docs]

    async def _log(
        self, conn: CalendarConnection, action: str, ok: bool, detail: str | None = None
    ) -> None:
        await self.store.put_doc(
            SyncLogEntry(
                tenant_id=conn.tenant_id, connection_id=conn.id, action=action, ok=ok, detail=detail
            ).to_doc()
        )
        conn.last_sync_at = datetime.now(UTC)
        conn.last_error = None if ok else detail
        if not ok and conn.provider != CalendarProvider.BOOKING_LINK:
            conn.status = ConnectionStatus.ERROR
        elif ok and conn.provider != CalendarProvider.BOOKING_LINK:
            conn.status = ConnectionStatus.CONNECTED
        await self.store.put_doc(conn.to_doc())

    # OAuth
    def oauth_backend(self, provider: CalendarProvider) -> OAuthBackend | None:
        b = self.backends.get(provider)
        return b if isinstance(b, GoogleCalendarBackend | MicrosoftCalendarBackend) else None

    async def oauth_start(
        self, tenant_id: str, company_id: str, provider: CalendarProvider, redirect_uri: str
    ) -> str:
        be = self.oauth_backend(provider)
        if be is None:
            raise ValueError(f"{provider} OAuth is not configured on this server")
        state = secrets.token_urlsafe(24)
        await self.store.put_doc(
            TenantDoc(
                kind=STATE_KIND,
                id=state,
                tenant_id=tenant_id,
                data={"company_id": company_id, "provider": provider, "redirect_uri": redirect_uri},
            )
        )
        return be.auth_url(state, redirect_uri)

    async def oauth_callback(self, state: str, code: str) -> CalendarConnection:
        st = await self.store.get_doc(STATE_KIND, state)
        if st is None:
            raise ValueError("unknown or expired OAuth state")
        await self.store.delete_doc(STATE_KIND, state)
        provider = CalendarProvider(st.data["provider"])
        be = self.oauth_backend(provider)
        if be is None:
            raise ValueError(f"{provider} OAuth is not configured on this server")
        tokens = await be.exchange_code(code, st.data["redirect_uri"])
        conn = CalendarConnection(
            tenant_id=st.tenant_id,
            company_id=st.data["company_id"],
            provider=provider,
            name=f"{provider.title()} Calendar",
            status=ConnectionStatus.CONNECTED,
            account_email=tokens.account_email,
            token_sealed=self.vault.seal(tokens.refresh_token),
        )
        await self.store.put_doc(conn.to_doc())
        await self._log(conn, "oauth", True, tokens.account_email)
        return conn

    # -- resources / scheduler resolution ---------------------------------------------------------
    async def _resolve_conn(
        self, tenant_id: str, connection_id: str | None
    ) -> CalendarConnection | None:
        return (
            await self.get(tenant_id, connection_id)
            if connection_id
            else await self.primary(tenant_id)
        )

    async def _scheduler(self, tenant_id: str) -> SchedulerConfig | None:
        if self.scheduler is None:
            return None
        settings = await self.resources.settings(tenant_id)
        if settings.mode != BookingMode.SCHEDULER:
            return None
        return await self.scheduler.active(tenant_id)

    async def team(self, tenant_id: str) -> list[Resource]:
        """Active resources that book into a calendar (an empty list = single-calendar tenant)."""
        return await self.resources.all(tenant_id, include_inactive=False)

    async def _conn_for(
        self, r: Resource, default: CalendarConnection, cache: dict[str, CalendarConnection | None]
    ) -> CalendarConnection | None:
        """The connection (with the resource's own calendar id) this resource books into."""
        base = default
        if r.connection_id and r.connection_id != default.id:
            if r.connection_id not in cache:
                cache[r.connection_id] = await self.get(r.tenant_id, r.connection_id)
            found = cache[r.connection_id]
            if found is None or not found.bookable:
                return None
            base = found
        return base.model_copy(update={"calendar_id": r.calendar_id or base.calendar_id})

    async def resource_hours(self, r: Resource, conn: CalendarConnection) -> Schedule:
        return r.hours if r.hours is not None else await self.booking_hours(conn)

    def _rules_conn(
        self, conn: CalendarConnection, default: CalendarConnection
    ) -> CalendarConnection:
        """Booking rules always come from the tenant's primary connection, whichever calendar."""
        return conn.model_copy(
            update={
                "rules": default.rules,
                "slot_minutes": default.slot_minutes,
                "buffer_minutes": default.buffer_minutes,
            }
        )

    async def _day_load(self, tenant_id: str, day: date, tz: str) -> Counter[str]:
        load: Counter[str] = Counter()
        for b in await self.bookings(tenant_id, 2000):
            if b.resource_id and b.status != "cancelled" and day_key(b.start, tz) == day:
                load[b.resource_id] += 1
        return load

    async def _preferred(self, tenant_id: str, phone: str | None) -> str | None:
        if not phone:
            return None
        for b in await self.bookings(tenant_id, 2000):
            if b.phone == phone and b.resource_id and b.status != "cancelled":
                return b.resource_id
        return None

    # -- availability & booking ---------------------------------------------------------------
    async def availability(
        self,
        tenant_id: str,
        *,
        connection_id: str | None = None,
        start: datetime | None = None,
        days: int = 7,
        duration_minutes: int | None = None,
        now: datetime | None = None,
        service_id: str | None = None,
        area: str | None = None,
    ) -> AvailabilityResult:
        now = now or datetime.now(UTC)
        start = start or now
        end = start + timedelta(days=days)
        conn = await self._resolve_conn(tenant_id, connection_id)
        sched = await self._scheduler(tenant_id)
        if sched is not None:
            return await self._scheduler_availability(
                sched, conn, start, end, now, duration_minutes, service_id, area
            )
        if conn is None:
            return AvailabilityResult(
                connection_id=None, provider=None, error="no calendar connected"
            )
        if conn.provider == CalendarProvider.BOOKING_LINK:
            return AvailabilityResult(
                connection_id=conn.id, provider=conn.provider, booking_url=conn.booking_url
            )
        be = self.backends.get(conn.provider)
        if be is None or not conn.bookable:
            return AvailabilityResult(
                connection_id=conn.id, provider=conn.provider, error="calendar not connected"
            )
        service = conn.rules.service(service_id)
        if service_id and service is None and conn.rules.services:
            return AvailabilityResult(
                connection_id=conn.id,
                provider=conn.provider,
                services=conn.rules.services,
                error=f"unknown service '{service_id}'",
            )
        team = await self.team(tenant_id)
        pool = (
            eligible(
                team,
                service_id=service.id if service else None,
                service_name=service.name if service else None,
                emergency=bool(service and service.emergency),
                prefer_on_call=(await self.resources.settings(tenant_id)).emergency_to_on_call,
            )
            if team
            else []
        )
        if team and not pool:
            return AvailabilityResult(
                connection_id=conn.id,
                provider=conn.provider,
                services=conn.rules.services,
                error="no one on the team is set up for that service",
            )
        if area and pool:
            covering = [r for r in pool if r.covers(area)]
            pool = covering or pool
        hours = await self.booking_hours(conn)
        length = slot_length(conn, duration_minutes, service)
        slots: list[Slot] = []
        cache: dict[str, CalendarConnection | None] = {}
        try:
            if not pool:
                busy = await be.busy(conn, start, end)
                slots = free_slots(
                    conn,
                    busy,
                    start,
                    end,
                    now=now,
                    duration_minutes=duration_minutes,
                    hours=hours,
                    service=service,
                )
            else:
                merged: dict[datetime, Slot] = {}
                for r in pool:
                    rconn = await self._conn_for(r, conn, cache)
                    if rconn is None:
                        continue
                    rbe = self.backends.get(rconn.provider)
                    if rbe is None:
                        continue
                    busy = await rbe.busy(rconn, start, end)
                    for s in free_slots(
                        self._rules_conn(rconn, conn),
                        busy,
                        start,
                        end,
                        now=now,
                        duration_minutes=duration_minutes,
                        hours=r.hours or hours,
                        service=service,
                        limit=60,
                    ):
                        merged.setdefault(s.start, s)
                slots = [merged[k] for k in sorted(merged)][:20]
        except Exception as e:
            await self._log(conn, "availability", False, str(e)[:300])
            return AvailabilityResult(
                connection_id=conn.id, provider=conn.provider, error=str(e)[:300]
            )
        await self._log(
            conn,
            "availability",
            True,
            f"{len(slots)} free slots" + (f" across {len(pool)} resources" if pool else ""),
        )
        return AvailabilityResult(
            connection_id=conn.id,
            provider=conn.provider,
            slots=slots,
            services=conn.rules.services,
            service_id=service.id if service else None,
            slot_minutes=int(length.total_seconds() // 60),
            resources=len(pool),
        )

    async def _scheduler_availability(
        self,
        sched: SchedulerConfig,
        conn: CalendarConnection | None,
        start: datetime,
        end: datetime,
        now: datetime,
        duration_minutes: int | None,
        service_id: str | None,
        area: str | None,
    ) -> AvailabilityResult:
        assert self.scheduler is not None
        rules = conn.rules if conn else BookingRules()
        service = rules.service(service_id)
        if service_id and service is None and rules.services:
            return AvailabilityResult(
                connection_id=None,
                provider=None,
                services=rules.services,
                source="scheduler",
                error=f"unknown service '{service_id}'",
            )
        minutes = service.minutes if service else (duration_minutes or sched.default_minutes)
        earliest = now + timedelta(minutes=rules.min_notice_minutes)
        horizon = min(end, now + timedelta(days=rules.max_days_ahead))
        try:
            ext = await self.scheduler.availability(
                sched,
                start=max(start, earliest),
                end=horizon,
                minutes=minutes,
                service=service.name if service else None,
                area=area,
            )
        except Exception as e:
            return AvailabilityResult(
                connection_id=None, provider=None, source="scheduler", error=str(e)[:300]
            )
        slots = [s for s in external_slots(ext) if s.start >= earliest and s.end <= horizon][:20]
        return AvailabilityResult(
            connection_id=conn.id if conn else None,
            provider=None,
            slots=slots,
            services=rules.services,
            service_id=service.id if service else None,
            slot_minutes=minutes,
            source="scheduler",
            resources=len({s.resource_id for s in ext if s.resource_id}),
        )

    async def book(self, tenant_id: str, req: BookingRequest) -> Booking:
        conn = await self._resolve_conn(tenant_id, req.connection_id)
        sched = await self._scheduler(tenant_id)
        if sched is not None:
            return await self._scheduler_book(sched, conn, tenant_id, req)
        if conn is None or conn.provider == CalendarProvider.BOOKING_LINK or not conn.bookable:
            raise ValueError("no bookable calendar connected")
        be = self.backends.get(conn.provider)
        if be is None:
            raise ValueError(f"{conn.provider} backend not configured")
        service = conn.rules.service(req.service_id)
        if req.service_id and service is None and conn.rules.services:
            raise ValueError(f"unknown service '{req.service_id}'")
        now = datetime.now(UTC)
        hours = await self.booking_hours(conn)
        length = slot_length(conn, req.duration_minutes, service)
        booking = self._new_booking(tenant_id, conn, req, length, service)
        team = await self.team(tenant_id)
        if not team:
            why = slot_allowed(
                conn,
                req.start,
                now=now,
                hours=hours,
                duration_minutes=req.duration_minutes,
                service=service,
            )
            if why is not None:
                await self._log(conn, "book", False, why)
                raise ValueError(why)
            if not await self._free(be, conn, booking):
                await self._log(conn, "book", False, "slot no longer free")
                raise ValueError("that slot is no longer available")
            target = conn
        else:
            settings = await self.resources.settings(tenant_id)
            pool = eligible(
                team,
                service_id=service.id if service else None,
                service_name=service.name if service else None,
                emergency=bool(service and service.emergency),
                prefer_on_call=settings.emergency_to_on_call,
            )
            if not pool:
                raise ValueError("no one on the team is set up for that service")
            area = outward_code(req.address)
            # Engineers covering the caller's area are tried first; anyone else on the team is
            # a fallback so a slot the caller was offered can still be booked.
            ordered = sorted(pool, key=lambda r: not r.covers(area)) if area else list(pool)
            cache: dict[str, CalendarConnection | None] = {}
            free: list[Candidate] = []
            reasons: list[str] = []
            for r in ordered:
                if free and area and not r.covers(area):
                    break
                rconn = await self._conn_for(r, conn, cache)
                if rconn is None:
                    continue
                why = slot_allowed(
                    self._rules_conn(rconn, conn),
                    req.start,
                    now=now,
                    hours=r.hours or hours,
                    duration_minutes=req.duration_minutes,
                    service=service,
                )
                if why is not None:
                    reasons.append(why)
                    continue
                rbe = self.backends.get(rconn.provider)
                if rbe is None:
                    continue
                if await self._free(rbe, self._rules_conn(rconn, conn), booking):
                    free.append(Candidate(resource=r, conn=rconn))
            if not free:
                why = (
                    reasons[0]
                    if reasons and len(reasons) == len(pool)
                    else "that slot is no longer available"
                )
                await self._log(conn, "book", False, why)
                raise ValueError(why)
            tz = hours.timezone
            chosen = choose(
                [c.resource for c in free],
                settings.policy,
                day_load=await self._day_load(tenant_id, day_key(req.start, tz), tz),
                cursor=settings.round_robin_cursor,
                postcode_area=area,
                preferred_id=await self._preferred(tenant_id, req.phone),
            )
            assert chosen is not None
            target = next(c.conn for c in free if c.resource.id == chosen.id)
            be = self.backends[target.provider]
            booking.resource_id, booking.resource_name = chosen.id, chosen.name
            booking.connection_id = target.id
            if settings.policy == AssignmentPolicy.ROUND_ROBIN:
                await self.resources.advance_round_robin(tenant_id)
        try:
            booking.provider_ref = await be.create_event(target, booking)
        except Exception as e:
            await self._log(conn, "book", False, str(e)[:300])
            raise
        await self.store.put_doc(booking.to_doc())
        await self._log(
            conn,
            "book",
            True,
            f"{booking.name} @ {booking.start.isoformat()}"
            + (f" -> {booking.resource_name}" if booking.resource_name else ""),
        )
        if self.on_booked is not None:
            await self.on_booked(booking)
        return booking

    def _new_booking(
        self,
        tenant_id: str,
        conn: CalendarConnection | None,
        req: BookingRequest,
        length: timedelta,
        service: ServiceType | None,
    ) -> Booking:
        return Booking(
            tenant_id=tenant_id,
            company_id=conn.company_id if conn else f"{tenant_id}-main",
            connection_id=conn.id if conn else "scheduler",
            call_id=req.call_id,
            start=req.start,
            end=req.start + length,
            name=req.name,
            phone=req.phone,
            notes=req.notes,
            address=req.address,
            caller_id=req.caller_id,
            call_link=(
                f"{self.dashboard_url}/calls/{req.call_id}"
                if req.call_id and self.dashboard_url
                else None
            ),
            service_id=service.id if service else None,
            service_name=service.name if service else None,
        )

    async def _free(self, be: CalendarBackend, conn: CalendarConnection, booking: Booking) -> bool:
        pad = timedelta(minutes=conn.buffer_minutes)
        busy = await be.busy(conn, booking.start - pad, booking.end + pad)
        padded = Slot(start=booking.start - pad, end=booking.end + pad)
        return not any(padded.overlaps(b) for b in busy)

    async def _scheduler_book(
        self,
        sched: SchedulerConfig,
        conn: CalendarConnection | None,
        tenant_id: str,
        req: BookingRequest,
    ) -> Booking:
        assert self.scheduler is not None
        rules = conn.rules if conn else BookingRules()
        service = rules.service(req.service_id)
        if req.service_id and service is None and rules.services:
            raise ValueError(f"unknown service '{req.service_id}'")
        minutes = service.minutes if service else (req.duration_minutes or sched.default_minutes)
        now = datetime.now(UTC)
        if req.start < now + timedelta(minutes=rules.min_notice_minutes):
            raise ValueError(f"bookings need at least {rules.min_notice_minutes} minutes' notice")
        if req.start > now + timedelta(days=rules.max_days_ahead):
            raise ValueError(f"bookings can be made up to {rules.max_days_ahead} days ahead")
        booking = self._new_booking(tenant_id, conn, req, timedelta(minutes=minutes), service)
        booking.source = "scheduler"
        team = {r.external_ref: r for r in await self.team(tenant_id) if r.external_ref}
        ext = await self.scheduler.availability(
            sched,
            start=req.start,
            end=req.start + timedelta(minutes=minutes),
            minutes=minutes,
            service=service.name if service else None,
            area=outward_code(req.address),
        )
        hit = next((s for s in ext if s.start == req.start), None)
        if hit is None:
            raise ValueError("that slot is no longer available")
        if hit.resource_id and hit.resource_id in team:
            booking.resource_id = team[hit.resource_id].id
            booking.resource_name = team[hit.resource_id].name
        booking.provider_ref = await self.scheduler.create(sched, booking, hit.resource_id)
        await self.store.put_doc(booking.to_doc())
        if self.on_booked is not None:
            await self.on_booked(booking)
        return booking

    # -- changes to existing bookings ---------------------------------------------------------
    async def booking(self, tenant_id: str, booking_id: str) -> Booking | None:
        d = await self.store.get_doc(BOOKING_KIND, booking_id)
        if d is None or d.tenant_id != tenant_id:
            return None
        return Booking.model_validate(d.data)

    async def _save_change(self, booking: Booking, action: str) -> Booking:
        booking.updated_at = datetime.now(UTC)
        await self.store.put_doc(booking.to_doc())
        if self.on_changed is not None:
            await self.on_changed(booking, action)
        return booking

    async def _move_event(
        self,
        booking: Booking,
        new_conn: CalendarConnection | None,
        old_conn: CalendarConnection | None,
    ) -> None:
        """Calendar mode: drop the old event and create the new one (or a scheduler update)."""
        sched = await self._scheduler(booking.tenant_id) if booking.source == "scheduler" else None
        if sched is not None and self.scheduler is not None:
            res = await self.resources.get(booking.tenant_id, booking.resource_id or "")
            await self.scheduler.update(sched, booking, res.external_ref if res else None)
            return
        if old_conn is not None and booking.provider_ref:
            old_be = self.backends.get(old_conn.provider)
            if old_be is not None:
                try:
                    await old_be.delete_event(old_conn, booking.provider_ref)
                except Exception as e:
                    await self._log(old_conn, "move", False, str(e)[:300])
                    raise
        if new_conn is not None:
            be = self.backends.get(new_conn.provider)
            if be is None:
                raise ValueError(f"{new_conn.provider} backend not configured")
            booking.provider_ref = await be.create_event(new_conn, booking)
            booking.connection_id = new_conn.id
            await self._log(
                new_conn, "move", True, f"{booking.name} -> {booking.start.isoformat()}"
            )

    async def _booking_conn(self, booking: Booking) -> CalendarConnection | None:
        conn = await self.get(booking.tenant_id, booking.connection_id)
        if conn is None:
            return None
        if booking.resource_id:
            res = await self.resources.get(booking.tenant_id, booking.resource_id)
            if res is not None:
                return conn.model_copy(update={"calendar_id": res.calendar_id})
        return conn

    @staticmethod
    def _require_owned(booking: Booking) -> None:
        """Scheduler-made bookings belong to the tenant's tool; change them there and the
        webhook brings the update back."""
        if booking.source == "scheduler":
            raise PermissionError("this booking is managed by your scheduling tool")

    async def reassign(self, tenant_id: str, booking_id: str, resource_id: str) -> Booking:
        booking = await self.booking(tenant_id, booking_id)
        if booking is None:
            raise LookupError("booking not found")
        if booking.status == "cancelled":
            raise ValueError("booking is cancelled")
        self._require_owned(booking)
        res = await self.resources.get(tenant_id, resource_id)
        if res is None or not res.active:
            raise ValueError("resource not found or inactive")
        primary = await self.primary(tenant_id)
        if primary is None:
            raise ValueError("no calendar connected")
        old_conn = await self._booking_conn(booking)
        new_conn = await self._conn_for(res, primary, {})
        if booking.source != "scheduler":
            if new_conn is None:
                raise ValueError("that resource has no bookable calendar")
            be = self.backends.get(new_conn.provider)
            if be is None:
                raise ValueError(f"{new_conn.provider} backend not configured")
            probe = booking.model_copy()
            if not await self._free(be, self._rules_conn(new_conn, primary), probe):
                raise ValueError(f"{res.name} is not free at that time")
        booking.resource_id, booking.resource_name = res.id, res.name
        await self._move_event(booking, new_conn, old_conn)
        return await self._save_change(booking, "reassigned")

    async def reschedule(self, tenant_id: str, booking_id: str, start: datetime) -> Booking:
        booking = await self.booking(tenant_id, booking_id)
        if booking is None:
            raise LookupError("booking not found")
        if booking.status == "cancelled":
            raise ValueError("booking is cancelled")
        self._require_owned(booking)
        length = booking.end - booking.start
        primary = await self.primary(tenant_id)
        old_conn = await self._booking_conn(booking)
        moved = booking.model_copy(update={"start": start, "end": start + length})
        if booking.source != "scheduler":
            if primary is None or old_conn is None:
                raise ValueError("no calendar connected")
            service = primary.rules.service(booking.service_id)
            res = (
                await self.resources.get(tenant_id, booking.resource_id)
                if booking.resource_id
                else None
            )
            hours = res.hours if res and res.hours else await self.booking_hours(primary)
            why = slot_allowed(primary, start, now=datetime.now(UTC), hours=hours, service=service)
            if why is not None:
                raise ValueError(why)
            be = self.backends.get(old_conn.provider)
            if be is None:
                raise ValueError(f"{old_conn.provider} backend not configured")
            busy = await be.busy(
                old_conn,
                start - timedelta(minutes=primary.buffer_minutes),
                moved.end + timedelta(minutes=primary.buffer_minutes),
            )
            own = Slot(start=booking.start, end=booking.end)
            pad = timedelta(minutes=primary.buffer_minutes)
            padded = Slot(start=start - pad, end=moved.end + pad)
            if any(
                padded.overlaps(b) and not (b.start == own.start and b.end == own.end) for b in busy
            ):
                raise ValueError("that slot is no longer available")
        booking.start, booking.end = moved.start, moved.end
        if booking.status == "reschedule_requested":
            booking.status = "confirmed"
        await self._move_event(booking, old_conn, old_conn)
        return await self._save_change(booking, "rescheduled")

    async def cancel(self, tenant_id: str, booking_id: str) -> Booking:
        booking = await self.booking(tenant_id, booking_id)
        if booking is None:
            raise LookupError("booking not found")
        if booking.status == "cancelled":
            return booking
        self._require_owned(booking)
        conn = await self._booking_conn(booking)
        be = self.backends.get(conn.provider) if conn else None
        if conn is not None and be is not None and booking.provider_ref:
            try:
                await be.delete_event(conn, booking.provider_ref)
            except Exception as e:
                await self._log(conn, "cancel", False, str(e)[:300])
                raise
            await self._log(conn, "cancel", True, booking.name)
        booking.status = "cancelled"
        return await self._save_change(booking, "cancelled")
