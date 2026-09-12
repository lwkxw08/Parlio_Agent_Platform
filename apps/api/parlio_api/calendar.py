"""Calendar & booking integration: mid-call availability checks and appointment booking.

Two shapes of integration share one `CalendarConnection` model:
- OAuth calendars (Google, Microsoft/Outlook): Parlio reads free/busy and creates events. The
  refresh token is sealed in the vault; only `has_token` is ever exposed.
- Booking links (Cal.com, Square, GoHighLevel, anything with a URL): no API access - the assistant
  texts the link (via the `booking_link` SMS scenario) instead of booking directly.

`CalendarBackend` is the provider seam; `SimulatedBackend` makes the whole flow testable offline.
Every provider call is appended to the sync log so the dashboard can show what happened.
"""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime, time, timedelta
from enum import StrEnum
from typing import Any, Protocol
from urllib.parse import urlencode
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel, Field

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
    provider_ref: str | None = None
    status: str = "confirmed"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

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
    call_id: str | None = None
    duration_minutes: int | None = None


class OAuthTokens(BaseModel):
    refresh_token: str
    account_email: str | None = None


# -- free slot computation -------------------------------------------------------------------


def free_slots(
    conn: CalendarConnection,
    busy: list[Slot],
    start: datetime,
    end: datetime,
    *,
    now: datetime | None = None,
    duration_minutes: int | None = None,
    limit: int = 20,
) -> list[Slot]:
    """Slots within `conn.hours` between start/end not overlapping busy periods or the past."""
    now = now or datetime.now(UTC)
    tz = ZoneInfo(conn.hours.timezone)
    length = timedelta(minutes=duration_minutes or conn.slot_minutes)
    step = timedelta(minutes=conn.slot_minutes)
    pad = timedelta(minutes=conn.buffer_minutes)
    out: list[Slot] = []
    day = start.astimezone(tz).date()
    last = end.astimezone(tz).date()
    while day <= last and len(out) < limit:
        if conn.hours.always:
            open_t, close_t = time(0, 0), time(23, 59)
        else:
            dh = conn.hours.hours.get(WEEKDAYS[day.weekday()])
            if dh is None:
                day += timedelta(days=1)
                continue
            open_t, close_t = dh.open, dh.close
        cursor = datetime.combine(day, open_t, tz)
        close = datetime.combine(day, close_t, tz)
        while cursor + length <= close and len(out) < limit:
            cand = Slot(start=cursor, end=cursor + length)
            padded = Slot(start=cursor - pad, end=cursor + length + pad)
            fits = cand.start >= max(now, start) and cand.end <= end
            if fits and not any(padded.overlaps(b) for b in busy):
                out.append(cand)
            cursor += step
        day += timedelta(days=1)
    return out


# -- provider backends -----------------------------------------------------------------------


class CalendarBackend(Protocol):
    provider: CalendarProvider

    async def busy(
        self, conn: CalendarConnection, start: datetime, end: datetime
    ) -> list[Slot]: ...
    async def create_event(self, conn: CalendarConnection, booking: Booking) -> str: ...


class OAuthBackend(CalendarBackend, Protocol):
    def auth_url(self, state: str, redirect_uri: str) -> str: ...
    async def exchange_code(self, code: str, redirect_uri: str) -> OAuthTokens: ...


class SimulatedBackend:
    provider = CalendarProvider.SIMULATED

    def __init__(self, busy: list[Slot] | None = None) -> None:
        self.busy_slots = busy or []
        self.events: list[Booking] = []
        self.fail = False

    async def busy(self, conn: CalendarConnection, start: datetime, end: datetime) -> list[Slot]:
        if self.fail:
            raise RuntimeError("simulated calendar outage")
        return [b for b in self.busy_slots if b.overlaps(Slot(start=start, end=end))]

    async def create_event(self, conn: CalendarConnection, booking: Booking) -> str:
        if self.fail:
            raise RuntimeError("simulated calendar outage")
        self.events.append(booking)
        self.busy_slots.append(Slot(start=booking.start, end=booking.end))
        return f"sim-{len(self.events)}"


class GoogleCalendarBackend:
    provider = CalendarProvider.GOOGLE
    AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN = "https://oauth2.googleapis.com/token"
    API = "https://www.googleapis.com/calendar/v3"
    SCOPES = "https://www.googleapis.com/auth/calendar openid email"

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
        desc = "\n".join(
            filter(None, [f"Phone: {booking.phone}" if booking.phone else None, booking.notes])
        )
        r = await self._http.post(
            f"{self.API}/calendars/{conn.calendar_id}/events",
            headers={"Authorization": f"Bearer {tok}"},
            json={
                "summary": f"{booking.name} (booked by Parlio)",
                "description": desc,
                "start": {"dateTime": booking.start.isoformat()},
                "end": {"dateTime": booking.end.isoformat()},
            },
        )
        r.raise_for_status()
        return str(r.json()["id"])


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

    async def busy(self, conn: CalendarConnection, start: datetime, end: datetime) -> list[Slot]:
        tok = await self._access_token(conn)
        r = await self._http.get(
            f"{self.GRAPH}/me/calendarView",
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
        body = "\n".join(
            filter(None, [f"Phone: {booking.phone}" if booking.phone else None, booking.notes])
        )
        r = await self._http.post(
            f"{self.GRAPH}/me/events",
            headers={"Authorization": f"Bearer {tok}"},
            json={
                "subject": f"{booking.name} (booked by Parlio)",
                "body": {"contentType": "text", "content": body},
                "start": {"dateTime": booking.start.astimezone(UTC).isoformat(), "timeZone": "UTC"},
                "end": {"dateTime": booking.end.astimezone(UTC).isoformat(), "timeZone": "UTC"},
            },
        )
        r.raise_for_status()
        return str(r.json()["id"])


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


class CalendarService:
    def __init__(
        self,
        store: CallStore,
        vault: Vault,
        backends: dict[CalendarProvider, CalendarBackend] | None = None,
    ) -> None:
        self.store = store
        self.vault = vault
        self.backends = backends or {}

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

    # availability & booking
    async def availability(
        self,
        tenant_id: str,
        *,
        connection_id: str | None = None,
        start: datetime | None = None,
        days: int = 7,
        duration_minutes: int | None = None,
        now: datetime | None = None,
    ) -> AvailabilityResult:
        conn = (
            await self.get(tenant_id, connection_id)
            if connection_id
            else await self.primary(tenant_id)
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
        now = now or datetime.now(UTC)
        start = start or now
        end = start + timedelta(days=days)
        try:
            busy = await be.busy(conn, start, end)
        except Exception as e:
            await self._log(conn, "availability", False, str(e)[:300])
            return AvailabilityResult(
                connection_id=conn.id, provider=conn.provider, error=str(e)[:300]
            )
        slots = free_slots(conn, busy, start, end, now=now, duration_minutes=duration_minutes)
        await self._log(conn, "availability", True, f"{len(slots)} free slots")
        return AvailabilityResult(connection_id=conn.id, provider=conn.provider, slots=slots)

    async def book(self, tenant_id: str, req: BookingRequest) -> Booking:
        conn = (
            await self.get(tenant_id, req.connection_id)
            if req.connection_id
            else await self.primary(tenant_id)
        )
        if conn is None or conn.provider == CalendarProvider.BOOKING_LINK or not conn.bookable:
            raise ValueError("no bookable calendar connected")
        be = self.backends.get(conn.provider)
        if be is None:
            raise ValueError(f"{conn.provider} backend not configured")
        length = timedelta(minutes=req.duration_minutes or conn.slot_minutes)
        booking = Booking(
            tenant_id=tenant_id,
            company_id=conn.company_id,
            connection_id=conn.id,
            call_id=req.call_id,
            start=req.start,
            end=req.start + length,
            name=req.name,
            phone=req.phone,
            notes=req.notes,
        )
        busy = await be.busy(
            conn, booking.start - timedelta(minutes=conn.buffer_minutes), booking.end
        )
        if any(Slot(start=booking.start, end=booking.end).overlaps(b) for b in busy):
            await self._log(conn, "book", False, "slot no longer free")
            raise ValueError("that slot is no longer available")
        try:
            booking.provider_ref = await be.create_event(conn, booking)
        except Exception as e:
            await self._log(conn, "book", False, str(e)[:300])
            raise
        await self.store.put_doc(booking.to_doc())
        await self._log(conn, "book", True, f"{booking.name} @ {booking.start.isoformat()}")
        return booking
