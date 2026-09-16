"""Phase 20b: SMS appointment reminder settings + log (dashboard)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from parlio_api.auth import UserDep
from parlio_api.deps import RemindersDep
from parlio_api.reminders import AppointmentReminder, ReminderPolicy

router = APIRouter(prefix="/v1/reminders", tags=["reminders"])


class PolicyUpdate(BaseModel):
    enabled: bool
    hours_before: list[int] = Field(default_factory=lambda: [24])
    template: str | None = None
    confirm_reply: str | None = None
    reschedule_reply: str | None = None
    timezone: str | None = None


@router.get("/policy", response_model=ReminderPolicy)
async def get_policy(user: UserDep, reminders: RemindersDep, tenant_id: str) -> ReminderPolicy:
    user.require_tenant(tenant_id)
    return await reminders.policy(tenant_id)


@router.put("/policy", response_model=ReminderPolicy)
async def put_policy(
    user: UserDep, reminders: RemindersDep, tenant_id: str, req: PolicyUpdate
) -> ReminderPolicy:
    user.require_tenant(tenant_id)
    if req.enabled and not req.hours_before:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "choose at least one offset")
    cur = await reminders.policy(tenant_id)
    upd = req.model_dump(exclude_none=True)
    return await reminders.set_policy(cur.model_copy(update=upd))


@router.get("", response_model=list[AppointmentReminder])
async def list_reminders(
    user: UserDep, reminders: RemindersDep, tenant_id: str, limit: int = 200
) -> list[AppointmentReminder]:
    user.require_tenant(tenant_id)
    out = await reminders.list_for(tenant_id, limit)
    out.sort(key=lambda r: r.send_at, reverse=True)
    return out
