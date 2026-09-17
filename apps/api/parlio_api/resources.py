"""Phase 22a/22b: bookable resources (engineers, technicians, rooms) and assignment policy.

A tenant with no resources keeps the single-calendar behaviour. Once resources exist, availability
is pooled across every active resource that can do the requested service and the booking is
assigned by the tenant's policy; the event lands on that resource's own calendar.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from parlio_api.store import CallStore, TenantDoc
from parlio_voice.models import Schedule

RESOURCE_KIND = "resource"
TEAM_KIND = "team_settings"

_OUTWARD = re.compile(r"^\s*([A-Za-z]{1,2}\d[A-Za-z\d]?)", re.I)


def outward_code(postcode_or_address: str | None) -> str | None:
    """UK outward code ('M1', 'SW1A') from a postcode; scans an address for the last postcode."""
    if not postcode_or_address:
        return None
    found: list[str] = re.findall(
        r"\b([A-Za-z]{1,2}\d[A-Za-z\d]?)\s*\d[A-Za-z]{2}\b", postcode_or_address
    )
    if found:
        return found[-1].upper()
    m = _OUTWARD.match(postcode_or_address)
    return m.group(1).upper() if m else None


def area_matches(area: str, code: str) -> bool:
    """'M' covers M1..M99; 'SW1' covers SW1A; exact outward codes match themselves."""
    a = area.strip().upper().replace(" ", "")
    return bool(a) and (code == a or code.startswith(a))


class AssignmentPolicy(StrEnum):
    LEAST_LOADED = "least_loaded"
    ROUND_ROBIN = "round_robin"
    NEAREST = "nearest"
    PREFERRED = "preferred"


class BookingMode(StrEnum):
    CALENDAR = "calendar"
    SCHEDULER = "scheduler"


class Resource(BaseModel):
    id: str = Field(default_factory=lambda: f"res-{uuid4().hex[:8]}")
    tenant_id: str
    name: str = Field(min_length=1, max_length=80)
    role: str = Field(default="Engineer", max_length=40)
    skills: list[str] = Field(
        default_factory=list, description="Service type ids/names this resource can do; empty = all"
    )
    site_id: str | None = None
    areas: list[str] = Field(
        default_factory=list, description="Postcode outward codes / prefixes covered (e.g. M, SK7)"
    )
    hours: Schedule | None = Field(
        default=None, description="Own shifts; None = follow the booking hours"
    )
    active: bool = True
    on_call: bool = False
    connection_id: str | None = Field(
        default=None, description="Calendar connection; None = tenant's primary"
    )
    calendar_id: str = Field(default="primary", description="Calendar within that connection")
    external_ref: str | None = Field(default=None, description="Id in the scheduling tool")
    phone: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def can_do(self, service_id: str | None, service_name: str | None = None) -> bool:
        if not self.skills or (service_id is None and service_name is None):
            return True
        keys = {s.strip().lower() for s in self.skills}
        return bool(
            (service_id and service_id.lower() in keys)
            or (service_name and service_name.strip().lower() in keys)
        )

    def covers(self, code: str | None) -> bool:
        if not self.areas:
            return True
        return code is not None and any(area_matches(a, code) for a in self.areas)

    def to_doc(self) -> TenantDoc:
        return TenantDoc(
            kind=RESOURCE_KIND,
            id=self.id,
            tenant_id=self.tenant_id,
            data=self.model_dump(mode="json"),
            created_at=self.created_at,
        )


class TeamSettings(BaseModel):
    tenant_id: str
    policy: AssignmentPolicy = AssignmentPolicy.LEAST_LOADED
    mode: BookingMode = BookingMode.CALENDAR
    emergency_to_on_call: bool = True
    round_robin_cursor: int = 0

    def to_doc(self) -> TenantDoc:
        return TenantDoc(
            kind=TEAM_KIND,
            id=self.tenant_id,
            tenant_id=self.tenant_id,
            data=self.model_dump(mode="json"),
        )


class ResourceInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    role: str = Field(default="Engineer", max_length=40)
    skills: list[str] = Field(default_factory=list)
    site_id: str | None = None
    areas: list[str] = Field(default_factory=list)
    hours: Schedule | None = None
    active: bool = True
    on_call: bool = False
    connection_id: str | None = None
    calendar_id: str = "primary"
    external_ref: str | None = None
    phone: str | None = None


class TeamSettingsInput(BaseModel):
    policy: AssignmentPolicy = AssignmentPolicy.LEAST_LOADED
    mode: BookingMode = BookingMode.CALENDAR
    emergency_to_on_call: bool = True


def eligible(
    resources: list[Resource],
    *,
    service_id: str | None = None,
    service_name: str | None = None,
    emergency: bool = False,
    prefer_on_call: bool = True,
    site_id: str | None = None,
) -> list[Resource]:
    """Active resources able to do the service (emergencies go to on-call staff when any)."""
    pool = [r for r in resources if r.active and r.can_do(service_id, service_name)]
    if site_id:
        pool = [r for r in pool if r.site_id in (None, site_id)]
    if emergency and prefer_on_call:
        on_call = [r for r in pool if r.on_call]
        if on_call:
            return on_call
    return pool


def choose(
    candidates: list[Resource],
    policy: AssignmentPolicy,
    *,
    day_load: Counter[str],
    cursor: int = 0,
    postcode_area: str | None = None,
    preferred_id: str | None = None,
) -> Resource | None:
    """Pick the assignee among resources already known to be free for the slot."""
    if not candidates:
        return None
    if policy == AssignmentPolicy.PREFERRED and preferred_id:
        hit = next((r for r in candidates if r.id == preferred_id), None)
        if hit is not None:
            return hit
    if policy == AssignmentPolicy.NEAREST and postcode_area:
        # Longest matching area prefix wins; ties fall through to least loaded.
        def score(r: Resource) -> int:
            return max((len(a) for a in r.areas if area_matches(a, postcode_area)), default=-1)

        best = max(score(r) for r in candidates)
        if best > 0:
            candidates = [r for r in candidates if score(r) == best]
    if policy == AssignmentPolicy.ROUND_ROBIN:
        ordered = sorted(candidates, key=lambda r: r.created_at)
        return ordered[cursor % len(ordered)]
    return min(candidates, key=lambda r: (day_load.get(r.id, 0), r.created_at))


class ResourceService:
    def __init__(self, store: CallStore) -> None:
        self.store = store

    async def all(self, tenant_id: str, *, include_inactive: bool = True) -> list[Resource]:
        docs = await self.store.list_docs(RESOURCE_KIND, tenant_id)
        out = sorted((Resource.model_validate(d.data) for d in docs), key=lambda r: r.created_at)
        return out if include_inactive else [r for r in out if r.active]

    async def get(self, tenant_id: str, rid: str) -> Resource | None:
        d = await self.store.get_doc(RESOURCE_KIND, rid)
        if d is None or d.tenant_id != tenant_id:
            return None
        return Resource.model_validate(d.data)

    async def put(self, r: Resource) -> Resource:
        await self.store.put_doc(r.to_doc())
        return r

    async def delete(self, tenant_id: str, rid: str) -> bool:
        if await self.get(tenant_id, rid) is None:
            return False
        return await self.store.delete_doc(RESOURCE_KIND, rid)

    async def settings(self, tenant_id: str) -> TeamSettings:
        d = await self.store.get_doc(TEAM_KIND, tenant_id)
        return TeamSettings.model_validate(d.data) if d else TeamSettings(tenant_id=tenant_id)

    async def put_settings(self, s: TeamSettings) -> TeamSettings:
        await self.store.put_doc(s.to_doc())
        return s

    async def advance_round_robin(self, tenant_id: str) -> None:
        s = await self.settings(tenant_id)
        s.round_robin_cursor = (s.round_robin_cursor + 1) % 1_000_000
        await self.put_settings(s)

    async def sync_external(
        self, tenant_id: str, staff: Sequence[dict[str, Any]]
    ) -> list[Resource]:
        """Upsert resources from a scheduling tool's staff list (matched on external_ref)."""
        existing = {r.external_ref: r for r in await self.all(tenant_id) if r.external_ref}
        out: list[Resource] = []
        for s in staff:
            ref = str(s.get("id") or "")
            if not ref:
                continue
            r = existing.get(ref) or Resource(tenant_id=tenant_id, name=str(s.get("name") or ref))
            r.external_ref = ref
            r.name = str(s.get("name") or r.name)
            if "active" in s:
                r.active = bool(s["active"])
            if s.get("skills") is not None:
                r.skills = [str(x) for x in s["skills"]]
            if s.get("areas") is not None:
                r.areas = [str(x) for x in s["areas"]]
            out.append(await self.put(r))
        return out


def day_key(t: datetime, tz: str) -> date:
    return t.astimezone(ZoneInfo(tz)).date()
