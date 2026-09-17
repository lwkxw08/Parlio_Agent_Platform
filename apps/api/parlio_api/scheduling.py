"""Phase 22c: the tenant's own scheduling tool as the booking system of record.

`SchedulingBackend` sits next to `CalendarBackend`: instead of free/busy on a calendar it asks the
tool for staff, availability and creates/updates/cancels jobs. Two adapters ship here:

- `WebhookScheduler`: a generic HMAC-signed HTTP contract for in-house systems
  (`GET /resources`, `GET /availability`, `POST /jobs`, `PATCH /jobs/{ref}`, `DELETE /jobs/{ref}`),
  plus an inbound event endpoint (`/v1/public/scheduling/events/{tenant}`) signed the same way.
- `ServiceM8Scheduler`: staff + job activities via the ServiceM8 REST API (the Phase 7 connector
  only pushed job cards out; this reads availability and books).

`SimulatedScheduler` keeps the flow testable offline.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol
from uuid import uuid4

import httpx
from pydantic import BaseModel, Field

from parlio_api.store import CallStore, TenantDoc
from parlio_api.vault import Vault

if TYPE_CHECKING:
    from parlio_api.calendar import Booking

log = logging.getLogger("parlio.api.scheduling")

SCHEDULER_KIND = "scheduler"
SIGNATURE_HEADER = "X-Parlio-Signature"
TIMESTAMP_HEADER = "X-Parlio-Timestamp"


class SchedulerProvider(StrEnum):
    WEBHOOK = "webhook"
    SERVICEM8 = "servicem8"
    SIMULATED = "simulated"


class SchedulerConfig(BaseModel):
    """One scheduling tool per tenant; the secret is sealed in the vault."""

    id: str = Field(default_factory=lambda: f"sch-{uuid4().hex[:8]}")
    tenant_id: str
    provider: SchedulerProvider
    name: str = "Scheduling tool"
    base_url: str | None = Field(default=None, description="Webhook contract base URL")
    secret_sealed: str | None = Field(default=None, exclude=True)
    enabled: bool = True
    read_only_schedule: bool = Field(
        default=True, description="Schedule view is read-only when the tool owns the diary"
    )
    default_minutes: int = Field(default=60, ge=5, le=480)
    last_sync_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def has_secret(self) -> bool:
        return bool(self.secret_sealed)

    def public(self) -> dict[str, Any]:
        return {**self.model_dump(mode="json"), "has_secret": self.has_secret}

    def to_doc(self) -> TenantDoc:
        data = self.model_dump(mode="json")
        data["secret_sealed"] = self.secret_sealed
        return TenantDoc(
            kind=SCHEDULER_KIND,
            id=self.tenant_id,
            tenant_id=self.tenant_id,
            data=data,
            created_at=self.created_at,
        )


class ExternalResource(BaseModel):
    id: str
    name: str
    active: bool = True
    skills: list[str] = Field(default_factory=list)
    areas: list[str] = Field(default_factory=list)


class ExternalShift(BaseModel):
    resource_id: str
    start: datetime
    end: datetime


class ExternalSlot(BaseModel):
    start: datetime
    end: datetime
    resource_id: str | None = None


class ExternalJob(BaseModel):
    """What the tool needs to create a job/appointment."""

    ref: str | None = None
    start: datetime
    end: datetime
    customer: str
    phone: str | None = None
    address: str | None = None
    notes: str | None = None
    service: str | None = None
    minutes: int
    resource_id: str | None = None
    booking_id: str
    call_link: str | None = None


class SchedulingBackend(Protocol):
    provider: SchedulerProvider

    async def resources(
        self, cfg: SchedulerConfig, secret: str | None
    ) -> list[ExternalResource]: ...
    async def shifts(
        self, cfg: SchedulerConfig, secret: str | None, start: datetime, end: datetime
    ) -> list[ExternalShift]: ...
    async def availability(
        self,
        cfg: SchedulerConfig,
        secret: str | None,
        *,
        start: datetime,
        end: datetime,
        minutes: int,
        service: str | None,
        area: str | None,
    ) -> list[ExternalSlot]: ...
    async def create_job(
        self, cfg: SchedulerConfig, secret: str | None, job: ExternalJob
    ) -> str: ...
    async def update_job(
        self, cfg: SchedulerConfig, secret: str | None, ref: str, job: ExternalJob
    ) -> None: ...
    async def cancel_job(self, cfg: SchedulerConfig, secret: str | None, ref: str) -> None: ...
    async def busy(
        self, cfg: SchedulerConfig, secret: str | None, start: datetime, end: datetime
    ) -> list[ExternalSlot]: ...


# -- signing ---------------------------------------------------------------------------------


def sign(secret: str, timestamp: str, body: bytes) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


def verify(secret: str, timestamp: str | None, signature: str | None, body: bytes) -> bool:
    if not timestamp or not signature:
        return False
    try:
        if abs(time.time() - int(timestamp)) > 300:
            return False
    except ValueError:
        return False
    return hmac.compare_digest(sign(secret, timestamp, body), signature)


# -- adapters --------------------------------------------------------------------------------


class SimulatedScheduler:
    provider = SchedulerProvider.SIMULATED

    def __init__(self) -> None:
        self.staff = [
            ExternalResource(id="ext-1", name="Sim Engineer 1"),
            ExternalResource(id="ext-2", name="Sim Engineer 2"),
        ]
        self.jobs: dict[str, ExternalJob] = {}
        self.cancelled: list[str] = []
        self.fail = False

    def _check(self) -> None:
        if self.fail:
            raise RuntimeError("simulated scheduler outage")

    async def resources(self, cfg: SchedulerConfig, secret: str | None) -> list[ExternalResource]:
        self._check()
        return list(self.staff)

    async def shifts(
        self, cfg: SchedulerConfig, secret: str | None, start: datetime, end: datetime
    ) -> list[ExternalShift]:
        self._check()
        out: list[ExternalShift] = []
        day = start.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        while day < end:
            if day.weekday() < 5:
                out.extend(
                    ExternalShift(
                        resource_id=s.id,
                        start=day + timedelta(hours=8),
                        end=day + timedelta(hours=17),
                    )
                    for s in self.staff
                )
            day += timedelta(days=1)
        return out

    async def availability(
        self,
        cfg: SchedulerConfig,
        secret: str | None,
        *,
        start: datetime,
        end: datetime,
        minutes: int,
        service: str | None,
        area: str | None,
    ) -> list[ExternalSlot]:
        self._check()
        out: list[ExternalSlot] = []
        for sh in await self.shifts(cfg, secret, start, end):
            cursor = max(sh.start, start)
            while cursor + timedelta(minutes=minutes) <= sh.end and len(out) < 40:
                slot_end = cursor + timedelta(minutes=minutes)
                taken = any(
                    j.resource_id == sh.resource_id and j.start < slot_end and cursor < j.end
                    for j in self.jobs.values()
                )
                if not taken and cursor >= start:
                    out.append(ExternalSlot(start=cursor, end=slot_end, resource_id=sh.resource_id))
                cursor += timedelta(minutes=minutes)
        out.sort(key=lambda s: s.start)
        return out

    async def create_job(self, cfg: SchedulerConfig, secret: str | None, job: ExternalJob) -> str:
        self._check()
        ref = f"simjob-{len(self.jobs) + 1}"
        if job.resource_id is None:
            free = [
                s.id
                for s in self.staff
                if not any(
                    j.resource_id == s.id and j.start < job.end and job.start < j.end
                    for j in self.jobs.values()
                )
            ]
            job.resource_id = free[0] if free else None
            if job.resource_id is None:
                raise ValueError("no staff free at that time")
        self.jobs[ref] = job.model_copy(update={"ref": ref})
        return ref

    async def update_job(
        self, cfg: SchedulerConfig, secret: str | None, ref: str, job: ExternalJob
    ) -> None:
        self._check()
        self.jobs[ref] = job.model_copy(update={"ref": ref})

    async def cancel_job(self, cfg: SchedulerConfig, secret: str | None, ref: str) -> None:
        self._check()
        self.jobs.pop(ref, None)
        self.cancelled.append(ref)

    async def busy(
        self, cfg: SchedulerConfig, secret: str | None, start: datetime, end: datetime
    ) -> list[ExternalSlot]:
        self._check()
        return [
            ExternalSlot(start=j.start, end=j.end, resource_id=j.resource_id)
            for j in self.jobs.values()
            if j.start < end and start < j.end
        ]


class WebhookScheduler:
    """Generic contract for a tenant's in-house system. Every request carries
    `X-Parlio-Timestamp` and `X-Parlio-Signature: sha256=HMAC(secret, "<ts>.<body>")`."""

    provider = SchedulerProvider.WEBHOOK

    def __init__(self, http: httpx.AsyncClient | None = None) -> None:
        self._http = http or httpx.AsyncClient(timeout=15)

    def _headers(self, secret: str | None, body: bytes) -> dict[str, str]:
        ts = str(int(time.time()))
        h = {
            "content-type": "application/json",
            TIMESTAMP_HEADER: ts,
            "user-agent": "Parlio-Scheduling/1.0",
        }
        if secret:
            h[SIGNATURE_HEADER] = sign(secret, ts, body)
        return h

    def _url(self, cfg: SchedulerConfig, path: str) -> str:
        if not cfg.base_url:
            raise ValueError("scheduling tool URL not set")
        return cfg.base_url.rstrip("/") + path

    @staticmethod
    def _items(j: Any, key: str) -> list[dict[str, Any]]:
        if isinstance(j, dict):
            j = j.get(key) or []
        return [x for x in j if isinstance(x, dict)]

    async def _call(
        self, cfg: SchedulerConfig, secret: str | None, method: str, path: str, payload: Any = None
    ) -> Any:
        body = json.dumps(payload or {}, default=str).encode() if method != "GET" else b""
        r = await self._http.request(
            method,
            self._url(cfg, path),
            headers=self._headers(secret, body),
            content=body if method != "GET" else None,
            params=payload if method == "GET" else None,
        )
        r.raise_for_status()
        return r.json() if r.content else {}

    async def resources(self, cfg: SchedulerConfig, secret: str | None) -> list[ExternalResource]:
        j = await self._call(cfg, secret, "GET", "/resources")
        return [ExternalResource.model_validate(x) for x in self._items(j, "resources")]

    async def shifts(
        self, cfg: SchedulerConfig, secret: str | None, start: datetime, end: datetime
    ) -> list[ExternalShift]:
        j = await self._call(
            cfg, secret, "GET", "/shifts", {"start": start.isoformat(), "end": end.isoformat()}
        )
        return [ExternalShift.model_validate(x) for x in self._items(j, "shifts")]

    async def availability(
        self,
        cfg: SchedulerConfig,
        secret: str | None,
        *,
        start: datetime,
        end: datetime,
        minutes: int,
        service: str | None,
        area: str | None,
    ) -> list[ExternalSlot]:
        q: dict[str, Any] = {"start": start.isoformat(), "end": end.isoformat(), "minutes": minutes}
        if service:
            q["service"] = service
        if area:
            q["area"] = area
        j = await self._call(cfg, secret, "GET", "/availability", q)
        return [ExternalSlot.model_validate(x) for x in self._items(j, "slots")]

    async def create_job(self, cfg: SchedulerConfig, secret: str | None, job: ExternalJob) -> str:
        j = await self._call(cfg, secret, "POST", "/jobs", job.model_dump(mode="json"))
        ref = j.get("ref") or j.get("id") if isinstance(j, dict) else None
        if not ref:
            raise ValueError("scheduling tool did not return a job reference")
        return str(ref)

    async def update_job(
        self, cfg: SchedulerConfig, secret: str | None, ref: str, job: ExternalJob
    ) -> None:
        await self._call(cfg, secret, "PATCH", f"/jobs/{ref}", job.model_dump(mode="json"))

    async def cancel_job(self, cfg: SchedulerConfig, secret: str | None, ref: str) -> None:
        await self._call(cfg, secret, "DELETE", f"/jobs/{ref}")

    async def busy(
        self, cfg: SchedulerConfig, secret: str | None, start: datetime, end: datetime
    ) -> list[ExternalSlot]:
        j = await self._call(
            cfg, secret, "GET", "/jobs", {"start": start.isoformat(), "end": end.isoformat()}
        )
        return [
            ExternalSlot(
                start=datetime.fromisoformat(x["start"]),
                end=datetime.fromisoformat(x["end"]),
                resource_id=x.get("resource_id"),
            )
            for x in self._items(j, "jobs")
        ]


class ServiceM8Scheduler:
    """ServiceM8: staff.json are the resources, jobactivity.json the diary; a booking is a job
    plus a scheduled activity for the assigned staff member."""

    provider = SchedulerProvider.SERVICEM8
    BASE = "https://api.servicem8.com/api_1.0"
    FMT = "%Y-%m-%d %H:%M:%S"

    def __init__(self, http: httpx.AsyncClient | None = None) -> None:
        self._http = http or httpx.AsyncClient(timeout=15)

    @staticmethod
    def _h(secret: str | None) -> dict[str, str]:
        if not secret:
            raise ValueError("ServiceM8 API key not set")
        return {"X-Api-Key": secret, "accept": "application/json"}

    @classmethod
    def _dt(cls, s: str) -> datetime:
        return datetime.strptime(s, cls.FMT).replace(tzinfo=UTC)

    async def resources(self, cfg: SchedulerConfig, secret: str | None) -> list[ExternalResource]:
        r = await self._http.get(
            f"{self.BASE}/staff.json", headers=self._h(secret), params={"$filter": "active eq 1"}
        )
        r.raise_for_status()
        return [
            ExternalResource(
                id=str(s["uuid"]),
                name=f"{s.get('first', '')} {s.get('last', '')}".strip() or str(s["uuid"]),
                active=bool(int(s.get("active", 1))),
            )
            for s in r.json()
        ]

    async def shifts(
        self, cfg: SchedulerConfig, secret: str | None, start: datetime, end: datetime
    ) -> list[ExternalShift]:
        # ServiceM8 has no shift API; staff are assumed available 08:00-17:00 Mon-Fri and the
        # tenant's own booking hours still apply on top.
        out: list[ExternalShift] = []
        staff = await self.resources(cfg, secret)
        day = start.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        while day < end:
            if day.weekday() < 5:
                out.extend(
                    ExternalShift(
                        resource_id=s.id,
                        start=day + timedelta(hours=8),
                        end=day + timedelta(hours=17),
                    )
                    for s in staff
                )
            day += timedelta(days=1)
        return out

    async def busy(
        self, cfg: SchedulerConfig, secret: str | None, start: datetime, end: datetime
    ) -> list[ExternalSlot]:
        r = await self._http.get(
            f"{self.BASE}/jobactivity.json",
            headers=self._h(secret),
            params={
                "$filter": (
                    f"start_date ge '{start.astimezone(UTC):{self.FMT}}' and "
                    f"start_date le '{end.astimezone(UTC):{self.FMT}}' and active eq 1"
                )
            },
        )
        r.raise_for_status()
        return [
            ExternalSlot(
                start=self._dt(a["start_date"]),
                end=self._dt(a["end_date"]),
                resource_id=a.get("staff_uuid") or None,
            )
            for a in r.json()
            if a.get("start_date") and a.get("end_date")
        ]

    async def availability(
        self,
        cfg: SchedulerConfig,
        secret: str | None,
        *,
        start: datetime,
        end: datetime,
        minutes: int,
        service: str | None,
        area: str | None,
    ) -> list[ExternalSlot]:
        busy = await self.busy(cfg, secret, start, end)
        out: list[ExternalSlot] = []
        for sh in await self.shifts(cfg, secret, start, end):
            cursor = max(sh.start, start)
            mine = [b for b in busy if b.resource_id == sh.resource_id]
            while cursor + timedelta(minutes=minutes) <= sh.end and len(out) < 60:
                slot_end = cursor + timedelta(minutes=minutes)
                if not any(b.start < slot_end and cursor < b.end for b in mine):
                    out.append(ExternalSlot(start=cursor, end=slot_end, resource_id=sh.resource_id))
                cursor += timedelta(minutes=minutes)
        out.sort(key=lambda s: s.start)
        return out

    async def _company(self, h: dict[str, str], job: ExternalJob) -> str:
        name = job.customer.replace("'", "")
        r = await self._http.get(
            f"{self.BASE}/company.json", headers=h, params={"$filter": f"name eq '{name}'"}
        )
        r.raise_for_status()
        found = r.json()
        if found:
            return str(found[0]["uuid"])
        r = await self._http.post(
            f"{self.BASE}/company.json", headers=h, json={"name": job.customer}
        )
        r.raise_for_status()
        uuid = r.headers.get("x-record-uuid", "")
        if job.phone:
            parts = job.customer.split(" ")
            await self._http.post(
                f"{self.BASE}/companycontact.json",
                headers=h,
                json={
                    "company_uuid": uuid,
                    "first": parts[0],
                    "last": " ".join(parts[1:]),
                    "mobile": job.phone,
                    "type": "JOB",
                },
            )
        return str(uuid)

    def _description(self, job: ExternalJob) -> str:
        rows = [
            ("Service", job.service),
            ("Customer", job.customer),
            ("Phone", job.phone),
            ("Details", job.notes),
            ("Call", job.call_link),
        ]
        return "\n".join(f"{k}: {v}" for k, v in rows if v) + "\nBooked by ParlioTec from a call."

    async def create_job(self, cfg: SchedulerConfig, secret: str | None, job: ExternalJob) -> str:
        h = self._h(secret)
        company = await self._company(h, job)
        r = await self._http.post(
            f"{self.BASE}/job.json",
            headers=h,
            json={
                "company_uuid": company,
                "status": "Work Order",
                "job_description": self._description(job),
                "job_address": job.address or "",
            },
        )
        r.raise_for_status()
        job_uuid = r.headers.get("x-record-uuid", "")
        r = await self._http.post(
            f"{self.BASE}/jobactivity.json",
            headers=h,
            json={
                "job_uuid": job_uuid,
                "staff_uuid": job.resource_id or "",
                "start_date": job.start.astimezone(UTC).strftime(self.FMT),
                "end_date": job.end.astimezone(UTC).strftime(self.FMT),
                "activity_was_scheduled": 1,
            },
        )
        r.raise_for_status()
        return f"{job_uuid}:{r.headers.get('x-record-uuid', '')}"

    async def update_job(
        self, cfg: SchedulerConfig, secret: str | None, ref: str, job: ExternalJob
    ) -> None:
        h = self._h(secret)
        _job_uuid, _, activity = ref.partition(":")
        if not activity:
            raise ValueError("no ServiceM8 activity on this booking")
        r = await self._http.post(
            f"{self.BASE}/jobactivity/{activity}.json",
            headers=h,
            json={
                "staff_uuid": job.resource_id or "",
                "start_date": job.start.astimezone(UTC).strftime(self.FMT),
                "end_date": job.end.astimezone(UTC).strftime(self.FMT),
            },
        )
        r.raise_for_status()

    async def cancel_job(self, cfg: SchedulerConfig, secret: str | None, ref: str) -> None:
        h = self._h(secret)
        job_uuid, _, activity = ref.partition(":")
        if activity:
            r = await self._http.delete(f"{self.BASE}/jobactivity/{activity}.json", headers=h)
            r.raise_for_status()
        if job_uuid:
            r = await self._http.post(
                f"{self.BASE}/job/{job_uuid}.json", headers=h, json={"status": "Cancelled"}
            )
            r.raise_for_status()


# -- service ---------------------------------------------------------------------------------


class SchedulerInput(BaseModel):
    provider: SchedulerProvider
    name: str = "Scheduling tool"
    base_url: str | None = None
    secret: str | None = Field(default=None, description="API key or HMAC secret")
    enabled: bool = True
    read_only_schedule: bool = True
    default_minutes: int = Field(default=60, ge=5, le=480)


class SchedulingService:
    def __init__(
        self,
        store: CallStore,
        vault: Vault,
        backends: dict[SchedulerProvider, SchedulingBackend] | None = None,
    ) -> None:
        self.store = store
        self.vault = vault
        self.backends: dict[SchedulerProvider, SchedulingBackend] = backends or {
            SchedulerProvider.SIMULATED: SimulatedScheduler(),
            SchedulerProvider.WEBHOOK: WebhookScheduler(),
            SchedulerProvider.SERVICEM8: ServiceM8Scheduler(),
        }

    async def config(self, tenant_id: str) -> SchedulerConfig | None:
        d = await self.store.get_doc(SCHEDULER_KIND, tenant_id)
        return SchedulerConfig.model_validate(d.data) if d else None

    async def put(self, tenant_id: str, body: SchedulerInput) -> SchedulerConfig:
        prev = await self.config(tenant_id)
        cfg = SchedulerConfig(
            tenant_id=tenant_id,
            provider=body.provider,
            name=body.name,
            base_url=body.base_url,
            enabled=body.enabled,
            read_only_schedule=body.read_only_schedule,
            default_minutes=body.default_minutes,
            secret_sealed=(
                self.vault.seal(body.secret)
                if body.secret
                else (prev.secret_sealed if prev and prev.provider == body.provider else None)
            ),
            created_at=prev.created_at if prev else datetime.now(UTC),
        )
        await self.store.put_doc(cfg.to_doc())
        return cfg

    async def delete(self, tenant_id: str) -> bool:
        return await self.store.delete_doc(SCHEDULER_KIND, tenant_id)

    def secret(self, cfg: SchedulerConfig) -> str | None:
        return self.vault.open(cfg.secret_sealed) if cfg.secret_sealed else None

    def backend(self, cfg: SchedulerConfig) -> SchedulingBackend:
        be = self.backends.get(cfg.provider)
        if be is None:
            raise ValueError(f"{cfg.provider} scheduling backend not configured")
        return be

    async def active(self, tenant_id: str) -> SchedulerConfig | None:
        cfg = await self.config(tenant_id)
        return cfg if cfg and cfg.enabled else None

    async def _mark(self, cfg: SchedulerConfig, error: str | None) -> None:
        cfg.last_sync_at = datetime.now(UTC)
        cfg.last_error = error
        await self.store.put_doc(cfg.to_doc())

    async def test(self, cfg: SchedulerConfig) -> list[ExternalResource]:
        try:
            staff = await self.backend(cfg).resources(cfg, self.secret(cfg))
        except Exception as e:
            await self._mark(cfg, str(e)[:300])
            raise
        await self._mark(cfg, None)
        return staff

    async def availability(
        self,
        cfg: SchedulerConfig,
        *,
        start: datetime,
        end: datetime,
        minutes: int,
        service: str | None,
        area: str | None,
    ) -> list[ExternalSlot]:
        try:
            slots = await self.backend(cfg).availability(
                cfg,
                self.secret(cfg),
                start=start,
                end=end,
                minutes=minutes,
                service=service,
                area=area,
            )
        except Exception as e:
            await self._mark(cfg, str(e)[:300])
            raise
        await self._mark(cfg, None)
        return slots

    def job_for(self, booking: Booking, resource_ref: str | None) -> ExternalJob:
        return ExternalJob(
            ref=booking.provider_ref,
            start=booking.start,
            end=booking.end,
            customer=booking.name,
            phone=booking.phone,
            address=booking.address,
            notes=booking.notes,
            service=booking.service_name,
            minutes=int((booking.end - booking.start).total_seconds() // 60),
            resource_id=resource_ref,
            booking_id=booking.id,
            call_link=booking.call_link,
        )

    async def create(self, cfg: SchedulerConfig, booking: Booking, resource_ref: str | None) -> str:
        try:
            ref = await self.backend(cfg).create_job(
                cfg, self.secret(cfg), self.job_for(booking, resource_ref)
            )
        except Exception as e:
            await self._mark(cfg, str(e)[:300])
            raise
        await self._mark(cfg, None)
        return ref

    async def update(
        self, cfg: SchedulerConfig, booking: Booking, resource_ref: str | None
    ) -> None:
        if not booking.provider_ref:
            return
        await self.backend(cfg).update_job(
            cfg, self.secret(cfg), booking.provider_ref, self.job_for(booking, resource_ref)
        )

    async def cancel(self, cfg: SchedulerConfig, booking: Booking) -> None:
        if booking.provider_ref:
            await self.backend(cfg).cancel_job(cfg, self.secret(cfg), booking.provider_ref)

    async def busy(
        self, cfg: SchedulerConfig, start: datetime, end: datetime
    ) -> list[ExternalSlot]:
        return await self.backend(cfg).busy(cfg, self.secret(cfg), start, end)

    async def shifts(
        self, cfg: SchedulerConfig, start: datetime, end: datetime
    ) -> list[ExternalShift]:
        return await self.backend(cfg).shifts(cfg, self.secret(cfg), start, end)

    def verify_event(self, cfg: SchedulerConfig, headers: dict[str, str], body: bytes) -> bool:
        secret = self.secret(cfg)
        if not secret:
            return False
        lower = {k.lower(): v for k, v in headers.items()}
        return verify(
            secret, lower.get(TIMESTAMP_HEADER.lower()), lower.get(SIGNATURE_HEADER.lower()), body
        )
