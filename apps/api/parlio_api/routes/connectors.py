"""Phase 7: connector management, sync log, inbound API keys, CSV export, customer inbound API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import PlainTextResponse, RedirectResponse
from pydantic import BaseModel, Field

from parlio_api.auth import UserDep
from parlio_api.connectors import (
    PAYLOAD_FIELDS,
    PROVIDER_INFO,
    PROVIDERS,
    Connector,
    Payload,
    Provider,
    SyncJob,
    Trigger,
    calls_csv,
    contacts_csv,
    tickets_csv,
)
from parlio_api.deps import ApiKeyDep, ConnectorsDep, SettingsDep, StoreDep, TicketsDep
from parlio_api.store import CallStore, ContactUpdate
from parlio_voice.models import TicketIntake, TicketPriority

router = APIRouter(prefix="/v1", tags=["connectors"])
public = APIRouter(prefix="/v1/public", tags=["public"])
inbound = APIRouter(prefix="/v1/inbound", tags=["inbound"])


async def _company(store: CallStore, tenant_id: str) -> str:
    cfgs = await store.list_assistants(tenant_id)
    return cfgs[0].company_id if cfgs else f"{tenant_id}-main"


class ConnectorInput(BaseModel):
    provider: Provider
    name: str = ""
    enabled: bool = True
    triggers: list[Trigger] = Field(
        default_factory=lambda: [Trigger.LEAD_QUALIFIED, Trigger.TICKET_CREATED]
    )
    qualified_only: bool = False
    target_url: str | None = None
    options: dict[str, str] = Field(default_factory=dict)
    field_map: dict[str, str] = Field(default_factory=dict)
    secret: str | None = Field(default=None, description="API key / token; write-only")


class ConnectorPatch(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    triggers: list[Trigger] | None = None
    qualified_only: bool | None = None
    target_url: str | None = None
    options: dict[str, str] | None = None
    field_map: dict[str, str] | None = None
    secret: str | None = None


def _validate(c: Connector) -> None:
    info = PROVIDER_INFO[c.provider]
    if info.auth == "url" and not c.target_url:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "target_url required")
    if info.auth == "api_key" and not c.has_secret:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "secret (API key/token) required")
    missing = [f for f in info.fields if f not in c.options and f != "sheet"]
    if missing:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"options missing: {', '.join(missing)}")


@router.get("/connectors/providers")
async def providers(cx: ConnectorsDep) -> dict[str, Any]:
    return {
        "providers": [
            p.model_dump()
            | {
                "available": p.provider in cx.backends
                or p.provider in (Provider.ZAPIER, Provider.MAKE)
            }
            for p in PROVIDERS
        ],
        "payload_fields": PAYLOAD_FIELDS,
        "triggers": list(Trigger),
    }


@router.get("/connectors")
async def list_connectors(user: UserDep, cx: ConnectorsDep, tenant_id: str) -> list[dict[str, Any]]:
    user.require_tenant(tenant_id)
    return [c.public() for c in await cx.list_all(tenant_id)]


@router.post("/connectors", status_code=status.HTTP_201_CREATED)
async def create_connector(
    user: UserDep, cx: ConnectorsDep, store: StoreDep, tenant_id: str, body: ConnectorInput
) -> dict[str, Any]:
    user.require_admin(tenant_id)
    if PROVIDER_INFO[body.provider].auth == "oauth":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "use /connectors/oauth/start for this provider"
        )
    c = Connector(
        tenant_id=tenant_id,
        company_id=await _company(store, tenant_id),
        **body.model_dump(exclude={"secret"}),
    )
    if body.secret:
        c.secret_sealed = cx.vault.seal(body.secret)
    _validate(c)
    await cx.put(c)
    await cx.test(c)
    return c.public()


@router.patch("/connectors/{cid}")
async def update_connector(
    user: UserDep, cx: ConnectorsDep, tenant_id: str, cid: str, body: ConnectorPatch
) -> dict[str, Any]:
    user.require_admin(tenant_id)
    c = await cx.get(tenant_id, cid)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "connector not found")
    upd = body.model_dump(exclude_none=True, exclude={"secret"})
    c = c.model_copy(update=upd)
    if body.secret:
        c.secret_sealed = cx.vault.seal(body.secret)
    _validate(c)
    return (await cx.put(c)).public()


@router.delete("/connectors/{cid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connector(user: UserDep, cx: ConnectorsDep, tenant_id: str, cid: str) -> None:
    user.require_admin(tenant_id)
    if not await cx.delete(tenant_id, cid):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "connector not found")


@router.post("/connectors/{cid}/test")
async def test_connector(
    user: UserDep, cx: ConnectorsDep, tenant_id: str, cid: str
) -> dict[str, Any]:
    user.require_admin(tenant_id)
    c = await cx.get(tenant_id, cid)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "connector not found")
    ok, detail = await cx.test(c)
    return {"ok": ok, "detail": detail, "connector": c.public()}


@router.post("/connectors/{cid}/send-sample")
async def send_sample(
    user: UserDep, cx: ConnectorsDep, store: StoreDep, tenant_id: str, cid: str
) -> dict[str, Any]:
    """Push a sample qualified-lead payload so the tenant can check the mapping end-to-end."""
    user.require_admin(tenant_id)
    c = await cx.get(tenant_id, cid)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "connector not found")
    cfgs = await store.list_assistants(tenant_id)
    p = Payload(
        event=Trigger.LEAD_QUALIFIED,
        tenant_id=tenant_id,
        business_name=cfgs[0].business_name if cfgs else "Sample Business",
        caller_name="Sam Sample",
        caller_phone="+447700900123",
        caller_email="sam@example.com",
        caller_type="new",
        summary="Sample lead from Parlio - asked for a quote for a boiler service next week.",
        reason="Quote request",
        department="Sales",
        priority="normal",
        duration_s=94,
        qualified=True,
        call_id="sample-call",
    )
    job = SyncJob(
        tenant_id=tenant_id,
        connector_id=c.id,
        provider=c.provider,
        event=p.event,
        payload=p.model_dump(mode="json"),
    )
    job = await cx.run_job(c, job)
    return job.model_dump(mode="json")


@router.get("/connectors/{cid}/signing-secret")
async def signing_secret(
    user: UserDep, cx: ConnectorsDep, tenant_id: str, cid: str
) -> dict[str, str | None]:
    user.require_admin(tenant_id)
    c = await cx.get(tenant_id, cid)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "connector not found")
    return {"signing_secret": cx.signing_secret(c)}


@router.get("/connectors/oauth/start")
async def oauth_start(
    user: UserDep,
    cx: ConnectorsDep,
    store: StoreDep,
    settings: SettingsDep,
    tenant_id: str,
    provider: Provider,
    spreadsheet_id: str | None = None,
    sheet: str | None = None,
) -> dict[str, str]:
    user.require_admin(tenant_id)
    c = Connector(
        tenant_id=tenant_id,
        company_id=await _company(store, tenant_id),
        provider=provider,
        options={k: v for k, v in {"spreadsheet_id": spreadsheet_id, "sheet": sheet}.items() if v},
    )
    redirect = f"{settings.public_api_url.rstrip('/')}/v1/public/connectors/oauth/callback"
    try:
        url = await cx.oauth_start(c, redirect)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    return {"url": url}


@public.get("/connectors/oauth/callback")
async def oauth_callback(
    cx: ConnectorsDep,
    settings: SettingsDep,
    state: str,
    code: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    dest = f"{settings.dashboard_url.rstrip('/')}/integrations?tab=connectors"
    if error or not code:
        return RedirectResponse(f"{dest}&connector=error&reason={error or 'no_code'}")
    try:
        await cx.oauth_callback(state, code)
    except Exception as e:
        return RedirectResponse(f"{dest}&connector=error&reason={type(e).__name__}")
    return RedirectResponse(f"{dest}&connector=connected")


# -- sync log -------------------------------------------------------------------------------


@router.get("/connectors/jobs")
async def sync_jobs(
    user: UserDep, cx: ConnectorsDep, tenant_id: str, limit: int = Query(100, le=500)
) -> list[dict[str, Any]]:
    user.require_tenant(tenant_id)
    return [j.model_dump(mode="json") for j in await cx.jobs(tenant_id, limit)]


@router.post("/connectors/jobs/{job_id}/retry")
async def retry_job(
    user: UserDep, cx: ConnectorsDep, tenant_id: str, job_id: str
) -> dict[str, Any]:
    user.require_admin(tenant_id)
    job = await cx.retry(tenant_id, job_id, force=True)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
    return job.model_dump(mode="json")


# -- inbound API keys -----------------------------------------------------------------------


class ApiKeyInput(BaseModel):
    name: str = Field(min_length=1, max_length=60)


@router.get("/api-keys")
async def list_api_keys(user: UserDep, cx: ConnectorsDep, tenant_id: str) -> list[dict[str, Any]]:
    user.require_admin(tenant_id)
    return [k.model_dump(mode="json", exclude={"key_hash"}) for k in await cx.api_keys(tenant_id)]


@router.post("/api-keys", status_code=status.HTTP_201_CREATED)
async def create_api_key(
    user: UserDep, cx: ConnectorsDep, tenant_id: str, body: ApiKeyInput
) -> dict[str, Any]:
    user.require_admin(tenant_id)
    k, raw = await cx.create_api_key(tenant_id, body.name)
    return k.model_dump(mode="json", exclude={"key_hash"}) | {"key": raw}


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(user: UserDep, cx: ConnectorsDep, tenant_id: str, key_id: str) -> None:
    user.require_admin(tenant_id)
    if not await cx.revoke_api_key(tenant_id, key_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "key not found")


# -- CSV export -----------------------------------------------------------------------------


@router.get("/export/{what}.csv", response_class=PlainTextResponse)
async def export_csv(
    user: UserDep, store: StoreDep, tenant_id: str, what: str, limit: int = Query(5000, le=50_000)
) -> PlainTextResponse:
    user.require_tenant(tenant_id)
    if what == "calls":
        body = calls_csv(await store.list_calls(tenant_id, limit))
    elif what == "contacts":
        body = contacts_csv(await store.list_contacts(tenant_id, limit=limit))
    elif what == "tickets":
        body = tickets_csv(await store.list_tickets(tenant_id, limit=limit))
    else:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "export calls|contacts|tickets")
    return PlainTextResponse(
        body,
        media_type="text/csv",
        headers={"content-disposition": f'attachment; filename="parlio-{what}.csv"'},
    )


# -- inbound API (customer systems -> Parlio) ------------------------------------------------


class InboundContact(BaseModel):
    phone: str = Field(pattern=r"^\+[1-9]\d{6,14}$")
    name: str | None = None
    email: str | None = None
    vip: bool | None = None
    notes: str | None = None


class InboundTicket(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)
    caller_name: str | None = None
    caller_number: str | None = None
    priority: TicketPriority = TicketPriority.NORMAL
    department: str | None = None


@inbound.get("/me")
async def inbound_me(key: ApiKeyDep) -> dict[str, Any]:
    return {"tenant_id": key.tenant_id, "key": key.name, "prefix": key.prefix}


@inbound.post("/contacts", status_code=status.HTTP_201_CREATED)
async def inbound_contact(key: ApiKeyDep, store: StoreDep, body: InboundContact) -> dict[str, Any]:
    cid, returning = await store.touch_contact(
        key.tenant_id, await _company(store, key.tenant_id), body.phone
    )
    upd = ContactUpdate(name=body.name, email=body.email, vip=body.vip, notes=body.notes)
    c = await store.update_contact(cid, upd)
    return {"id": cid, "existing": returning, "contact": c.model_dump(mode="json") if c else None}


@inbound.post("/tickets", status_code=status.HTTP_201_CREATED)
async def inbound_ticket(
    key: ApiKeyDep, store: StoreDep, tickets: TicketsDep, body: InboundTicket
) -> dict[str, Any]:
    intake = TicketIntake(
        caller_name=body.caller_name,
        caller_number=body.caller_number,
        reason=body.reason,
        priority=body.priority,
        department=body.department,
        source="manual",
    )
    t = await tickets.create_from_intake(
        key.tenant_id, await _company(store, key.tenant_id), intake
    )
    return t.model_dump(mode="json")


@inbound.get("/calls")
async def inbound_calls(
    key: ApiKeyDep, store: StoreDep, limit: int = Query(50, le=500)
) -> list[dict[str, Any]]:
    return [c.model_dump(mode="json") for c in await store.list_calls(key.tenant_id, limit)]
