"""Phase 7 adapter framework: push Parlio events into third-party systems.

A `Connector` is a per-tenant link to one external system (CRM, sheet, chat, automation hub or
job-management tool). Every call/ticket/booking becomes a normalised `Payload` that is mapped
through the connector's `field_map` and handed to a `ConnectorBackend`. Each attempt is a
`SyncJob` (queued -> sent | retry | failed) so the dashboard can show what went where, and the
`RetryLoop` re-drives failed jobs with exponential back-off. Credentials are sealed in the vault
and never leave the API.

Backends are deliberately thin HTTP clients over stable vendor REST APIs; anything vendor-specific
that a tenant can tune (pipeline, owner, board) lives in `Connector.options`.
"""

from __future__ import annotations

import asyncio
import contextlib
import csv
import hashlib
import hmac
import io
import json
import logging
import secrets
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol
from urllib.parse import urlencode
from uuid import uuid4

import httpx
from pydantic import BaseModel, Field

from parlio_api.calendar import Booking, OAuthTokens
from parlio_api.store import CallRecord, CallStore, Contact, TenantDoc, Ticket
from parlio_api.vault import Vault

log = logging.getLogger("parlio.api.connectors")

CONN_KIND = "connector"
JOB_KIND = "sync_job"
APIKEY_KIND = "tenant_api_key"
STATE_KIND = "connector_oauth_state"

MAX_ATTEMPTS = 5
BACKOFF_S = (60, 300, 1800, 7200)  # 1m, 5m, 30m, 2h


class Provider(StrEnum):
    WEBHOOK = "webhook"  # generic JSON POST (also Zapier / Make catch hooks)
    ZAPIER = "zapier"
    MAKE = "make"
    GOOGLE_SHEETS = "google_sheets"
    HUBSPOT = "hubspot"
    SALESFORCE = "salesforce"
    PIPEDRIVE = "pipedrive"
    ZOHO = "zoho"
    TEAMS = "teams"
    SERVICEM8 = "servicem8"
    SIMULATED = "simulated"


class Trigger(StrEnum):
    CALL_COMPLETED = "call.completed"
    LEAD_QUALIFIED = "lead.qualified"
    TICKET_CREATED = "ticket.created"
    BOOKING_CREATED = "booking.created"


class JobStatus(StrEnum):
    QUEUED = "queued"
    SENT = "sent"
    RETRY = "retry"
    FAILED = "failed"
    SKIPPED = "skipped"


class ProviderInfo(BaseModel):
    provider: Provider
    label: str
    category: str  # automation | crm | sheet | chat | field_service | test
    auth: str  # url | api_key | oauth | none
    fields: list[str] = Field(default_factory=list, description="required option keys")
    help: str = ""


PROVIDERS: list[ProviderInfo] = [
    ProviderInfo(
        provider=Provider.WEBHOOK,
        label="Webhook (generic)",
        category="automation",
        auth="url",
        help="JSON POST per event, signed with X-Parlio-Signature (HMAC-SHA256 of the body).",
    ),
    ProviderInfo(
        provider=Provider.ZAPIER,
        label="Zapier",
        category="automation",
        auth="url",
        help="Create a Zap with the 'Webhooks by Zapier' catch-hook trigger and paste its URL.",
    ),
    ProviderInfo(
        provider=Provider.MAKE,
        label="Make (Integromat)",
        category="automation",
        auth="url",
        help="Add a 'Custom webhook' module in Make and paste its URL.",
    ),
    ProviderInfo(
        provider=Provider.GOOGLE_SHEETS,
        label="Google Sheets",
        category="sheet",
        auth="oauth",
        fields=["spreadsheet_id", "sheet"],
        help="Appends one row per event to the sheet tab (default 'Parlio').",
    ),
    ProviderInfo(
        provider=Provider.HUBSPOT,
        label="HubSpot",
        category="crm",
        auth="api_key",
        help="Private-app access token (Settings > Integrations > Private apps) with crm.objects."
        "contacts and crm.objects.calls read/write scopes. Upserts the contact and logs the call.",
    ),
    ProviderInfo(
        provider=Provider.SALESFORCE,
        label="Salesforce",
        category="crm",
        auth="api_key",
        fields=["instance_url", "client_id"],
        help="Connected App with the client-credentials flow; secret = consumer secret. Creates a "
        "Lead if no Lead/Contact matches the phone, then logs a completed-call Task.",
    ),
    ProviderInfo(
        provider=Provider.PIPEDRIVE,
        label="Pipedrive",
        category="crm",
        auth="api_key",
        fields=["company_domain"],
        help="Personal API token (Settings > Personal preferences > API). Upserts the person and "
        "logs a call activity.",
    ),
    ProviderInfo(
        provider=Provider.ZOHO,
        label="Zoho CRM",
        category="crm",
        auth="api_key",
        fields=["accounts_domain", "client_id"],
        help="Self-client refresh token (api-console.zoho.eu) as the secret; client id/secret in "
        "options. Upserts a Lead and attaches a Note.",
    ),
    ProviderInfo(
        provider=Provider.TEAMS,
        label="Microsoft Teams",
        category="chat",
        auth="url",
        help="Channel > Workflows > 'Post to a channel when a webhook request is received'; paste "
        "the URL. Posts an Adaptive Card per event.",
    ),
    ProviderInfo(
        provider=Provider.SERVICEM8,
        label="ServiceM8",
        category="field_service",
        auth="api_key",
        help="Private application API key. Creates/updates the client and raises a quote-stage "
        "job with the call summary. Reference adapter for trades tools (Jobber, simPRO, Tradify).",
    ),
    ProviderInfo(
        provider=Provider.SIMULATED,
        label="Demo (no account)",
        category="test",
        auth="none",
        help="Records events locally so you can see the sync log without a real account.",
    ),
]
PROVIDER_INFO = {p.provider: p for p in PROVIDERS}

# Canonical payload keys tenants can map onto vendor field names.
PAYLOAD_FIELDS = [
    "event",
    "occurred_at",
    "business_name",
    "caller_name",
    "caller_phone",
    "caller_email",
    "caller_type",
    "summary",
    "reason",
    "department",
    "priority",
    "duration_s",
    "qualified",
    "call_id",
    "ticket_id",
    "booking_start",
    "booking_end",
    "recording_url",
    "transcript_url",
]


class Payload(BaseModel):
    event: Trigger
    tenant_id: str
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    business_name: str = ""
    caller_name: str | None = None
    caller_phone: str | None = None
    caller_email: str | None = None
    caller_type: str | None = None
    summary: str | None = None
    reason: str | None = None
    department: str | None = None
    priority: str | None = None
    duration_s: float | None = None
    qualified: bool = False
    call_id: str | None = None
    ticket_id: str | None = None
    booking_start: datetime | None = None
    booking_end: datetime | None = None
    recording_url: str | None = None
    transcript_url: str | None = None
    extracted: dict[str, Any] = Field(default_factory=dict)

    def flat(self) -> dict[str, Any]:
        d = self.model_dump(mode="json", exclude={"extracted", "tenant_id"})
        for k, v in self.extracted.items():
            d.setdefault(f"extracted.{k}", v)
        return d

    def mapped(self, field_map: dict[str, str]) -> dict[str, Any]:
        """Apply a tenant field map {vendor_field: payload_key}; unmapped keys pass through."""
        flat = self.flat()
        if not field_map:
            return flat
        return {dst: flat.get(src) for dst, src in field_map.items()}

    def title(self) -> str:
        who = self.caller_name or self.caller_phone or "Unknown caller"
        match self.event:
            case Trigger.LEAD_QUALIFIED:
                return f"Qualified lead: {who}"
            case Trigger.TICKET_CREATED:
                return f"New {self.priority or 'normal'} ticket from {who}"
            case Trigger.BOOKING_CREATED:
                return f"Booking: {who}"
            case _:
                return f"Call from {who}"

    def text(self) -> str:
        bits = [self.summary or self.reason or "", f"Phone: {self.caller_phone or '-'}"]
        if self.caller_email:
            bits.append(f"Email: {self.caller_email}")
        if self.department:
            bits.append(f"Department: {self.department}")
        if self.booking_start:
            bits.append(f"When: {self.booking_start.strftime('%a %d %b %H:%M')}")
        if self.duration_s:
            bits.append(f"Duration: {int(self.duration_s)}s")
        if self.recording_url:
            bits.append(f"Recording: {self.recording_url}")
        bits.append(f"Via Parlio for {self.business_name}")
        return "\n".join(b for b in bits if b)


class Connector(BaseModel):
    id: str = Field(default_factory=lambda: f"cx-{uuid4().hex[:8]}")
    tenant_id: str
    company_id: str
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
    secret_sealed: str | None = Field(default=None, exclude=True)
    signing_secret_sealed: str | None = Field(default=None, exclude=True)
    account_label: str | None = None
    status: str = "pending"  # pending | connected | error
    last_sync_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def has_secret(self) -> bool:
        return bool(self.secret_sealed)

    def wants(self, p: Payload) -> bool:
        if not self.enabled or p.event not in self.triggers:
            return False
        return not (self.qualified_only and p.event == Trigger.CALL_COMPLETED and not p.qualified)

    def public(self) -> dict[str, Any]:
        return {**self.model_dump(mode="json"), "has_secret": self.has_secret}

    def to_doc(self) -> TenantDoc:
        data = self.model_dump(mode="json")
        data["secret_sealed"] = self.secret_sealed
        data["signing_secret_sealed"] = self.signing_secret_sealed
        return TenantDoc(
            kind=CONN_KIND,
            id=self.id,
            tenant_id=self.tenant_id,
            data=data,
            created_at=self.created_at,
        )


class SyncJob(BaseModel):
    id: str = Field(default_factory=lambda: f"sj-{uuid4().hex[:10]}")
    tenant_id: str
    connector_id: str
    provider: Provider
    event: Trigger
    status: JobStatus = JobStatus.QUEUED
    attempts: int = 0
    next_attempt_at: datetime | None = None
    external_ref: str | None = None
    error: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def to_doc(self) -> TenantDoc:
        return TenantDoc(
            kind=JOB_KIND,
            id=self.id,
            tenant_id=self.tenant_id,
            data=self.model_dump(mode="json"),
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


class TenantApiKey(BaseModel):
    """Inbound API key so customers' own systems can create tickets/contacts via /v1/inbound."""

    id: str = Field(default_factory=lambda: f"ak-{uuid4().hex[:8]}")
    tenant_id: str
    name: str
    prefix: str
    key_hash: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_used_at: datetime | None = None
    revoked: bool = False

    def to_doc(self) -> TenantDoc:
        return TenantDoc(
            kind=APIKEY_KIND,
            id=self.id,
            tenant_id=self.tenant_id,
            data=self.model_dump(mode="json"),
            created_at=self.created_at,
        )


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


# -- payload builders --------------------------------------------------------------------------


def payload_from_call(
    call: CallRecord, business_name: str, qualified: bool, public_url: str
) -> Payload:
    ex = call.extracted
    return Payload(
        event=Trigger.LEAD_QUALIFIED if qualified else Trigger.CALL_COMPLETED,
        tenant_id=call.tenant_id,
        occurred_at=call.ended_at or call.started_at,
        business_name=business_name,
        caller_name=ex.get("name"),
        caller_phone=call.caller,
        caller_email=ex.get("email"),
        caller_type=call.caller_type,
        summary=call.summary,
        reason=ex.get("reason"),
        duration_s=call.duration_s,
        qualified=qualified,
        call_id=call.call_id,
        recording_url=call.recordings[0] if call.recordings else None,
        transcript_url=(
            f"{public_url.rstrip('/')}/v1/public/share/{call.share_token}"
            if call.share_token
            else None
        ),
        extracted={k: v for k, v in ex.items() if k not in ("name", "email", "reason")},
    )


def payload_from_ticket(t: Ticket, business_name: str) -> Payload:
    return Payload(
        event=Trigger.TICKET_CREATED,
        tenant_id=t.tenant_id,
        occurred_at=t.created_at,
        business_name=business_name,
        caller_name=t.caller_name,
        caller_phone=t.caller_number,
        reason=t.reason,
        summary=t.reason,
        department=t.department,
        priority=t.priority.value,
        call_id=t.call_id,
        ticket_id=t.id,
        qualified=True,
    )


def payload_from_booking(b: Booking, business_name: str) -> Payload:
    return Payload(
        event=Trigger.BOOKING_CREATED,
        tenant_id=b.tenant_id,
        occurred_at=b.created_at,
        business_name=business_name,
        caller_name=b.name,
        caller_phone=b.phone,
        summary=b.notes or f"Appointment booked for {b.name}",
        call_id=b.call_id,
        booking_start=b.start,
        booking_end=b.end,
        qualified=True,
    )


# -- backends ----------------------------------------------------------------------------------


class ConnectorBackend(Protocol):
    provider: Provider

    async def test(self, c: Connector, secret: str | None) -> str:
        """Verify credentials; return a human label (account/portal name)."""
        ...

    async def push(self, c: Connector, secret: str | None, p: Payload) -> str | None:
        """Deliver one payload; return the external record reference."""
        ...


class OAuthConnectorBackend(ConnectorBackend, Protocol):
    def auth_url(self, state: str, redirect_uri: str) -> str: ...
    async def exchange_code(self, code: str, redirect_uri: str) -> OAuthTokens: ...


def _auth(tok: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {tok}"}


def _need(secret: str | None, what: str = "credentials") -> str:
    if not secret:
        raise RuntimeError(f"{what} missing - reconnect this integration")
    return secret


class SimulatedBackend:
    provider = Provider.SIMULATED

    def __init__(self) -> None:
        self.pushed: list[tuple[str, Payload]] = []
        self.fail_next = 0

    async def test(self, c: Connector, secret: str | None) -> str:
        return "demo account"

    async def push(self, c: Connector, secret: str | None, p: Payload) -> str | None:
        if self.fail_next:
            self.fail_next -= 1
            raise RuntimeError("simulated outage")
        self.pushed.append((c.id, p))
        return f"sim-{len(self.pushed)}"


class WebhookBackend:
    """Generic JSON POST; covers Zapier and Make catch hooks (same shape, different label)."""

    def __init__(self, http: httpx.AsyncClient, provider: Provider = Provider.WEBHOOK) -> None:
        self._http = http
        self.provider = provider

    def _body(self, c: Connector, p: Payload) -> dict[str, Any]:
        return {
            "id": f"evt-{uuid4().hex[:12]}",
            "event": p.event,
            "occurred_at": p.occurred_at.isoformat(),
            "tenant_id": p.tenant_id,
            "data": p.mapped(c.field_map),
        }

    async def _post(self, c: Connector, secret: str | None, body: dict[str, Any]) -> httpx.Response:
        url = _need(c.target_url, "webhook URL")
        raw = json.dumps(body, separators=(",", ":"), default=str).encode()
        headers = {"content-type": "application/json", "user-agent": "Parlio-Webhooks/1.0"}
        if secret:
            sig = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
            headers["X-Parlio-Signature"] = f"sha256={sig}"
        r = await self._http.post(url, content=raw, headers=headers)
        r.raise_for_status()
        return r

    async def test(self, c: Connector, secret: str | None) -> str:
        body = {
            "id": f"evt-{uuid4().hex[:12]}",
            "event": "connector.test",
            "occurred_at": datetime.now(UTC).isoformat(),
            "tenant_id": c.tenant_id,
            "data": {k: None for k in PAYLOAD_FIELDS} | {"event": "connector.test"},
        }
        r = await self._post(c, secret, body)
        return f"HTTP {r.status_code}"

    async def push(self, c: Connector, secret: str | None, p: Payload) -> str | None:
        r = await self._post(c, secret, self._body(c, p))
        try:
            j = r.json()
            return str(j.get("id") or j.get("request_id") or j.get("status") or r.status_code)
        except Exception:
            return str(r.status_code)


class TeamsBackend:
    """Teams Workflows webhook -> Adaptive Card."""

    provider = Provider.TEAMS

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    @staticmethod
    def card(title: str, text: str, facts: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": {
                        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                        "type": "AdaptiveCard",
                        "version": "1.4",
                        "body": [
                            {
                                "type": "TextBlock",
                                "size": "Large",
                                "weight": "Bolder",
                                "text": title,
                            },
                            {"type": "TextBlock", "wrap": True, "text": text},
                            {
                                "type": "FactSet",
                                "facts": [
                                    {"title": k, "value": str(v)} for k, v in facts.items() if v
                                ],
                            },
                        ],
                    },
                }
            ],
        }

    async def test(self, c: Connector, secret: str | None) -> str:
        r = await self._http.post(
            _need(c.target_url, "Teams webhook URL"),
            json=self.card("Parlio connected", "Alerts from your AI receptionist land here.", {}),
        )
        r.raise_for_status()
        return f"HTTP {r.status_code}"

    async def push(self, c: Connector, secret: str | None, p: Payload) -> str | None:
        facts = {
            "Phone": p.caller_phone,
            "Email": p.caller_email,
            "Department": p.department,
            "Priority": p.priority,
            "Recording": p.recording_url,
        }
        r = await self._http.post(
            _need(c.target_url, "Teams webhook URL"), json=self.card(p.title(), p.text(), facts)
        )
        r.raise_for_status()
        return str(r.status_code)


class HubSpotBackend:
    provider = Provider.HUBSPOT
    BASE = "https://api.hubapi.com"

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def test(self, c: Connector, secret: str | None) -> str:
        r = await self._http.get(
            f"{self.BASE}/account-info/v3/details", headers=_auth(_need(secret, "access token"))
        )
        r.raise_for_status()
        j = r.json()
        return f"portal {j.get('portalId')} ({j.get('uiDomain', 'hubspot')})"

    async def _find_contact(self, tok: str, phone: str | None, email: str | None) -> str | None:
        filters = []
        if phone:
            filters.append({"propertyName": "phone", "operator": "EQ", "value": phone})
        if email:
            filters.append({"propertyName": "email", "operator": "EQ", "value": email})
        if not filters:
            return None
        r = await self._http.post(
            f"{self.BASE}/crm/v3/objects/contacts/search",
            headers=_auth(tok),
            json={"filterGroups": [{"filters": [f]} for f in filters], "limit": 1},
        )
        r.raise_for_status()
        res = r.json().get("results", [])
        return str(res[0]["id"]) if res else None

    async def push(self, c: Connector, secret: str | None, p: Payload) -> str | None:
        tok = _need(secret, "access token")
        cid = await self._find_contact(tok, p.caller_phone, p.caller_email)
        props: dict[str, Any] = {"phone": p.caller_phone, "email": p.caller_email}
        if p.caller_name:
            first, _, last = p.caller_name.partition(" ")
            props |= {"firstname": first, "lastname": last or None}
        props |= p.mapped(c.field_map) if c.field_map else {}
        props = {k: v for k, v in props.items() if v is not None}
        if cid is None:
            props.setdefault("hs_lead_status", "NEW")
            props.setdefault("lifecyclestage", "lead")
            r = await self._http.post(
                f"{self.BASE}/crm/v3/objects/contacts",
                headers=_auth(tok),
                json={"properties": props},
            )
            r.raise_for_status()
            cid = str(r.json()["id"])
        elif props:
            await self._http.patch(
                f"{self.BASE}/crm/v3/objects/contacts/{cid}",
                headers=_auth(tok),
                json={"properties": props},
            )
        ts = int(p.occurred_at.timestamp() * 1000)
        r = await self._http.post(
            f"{self.BASE}/crm/v3/objects/calls",
            headers=_auth(tok),
            json={
                "properties": {
                    "hs_timestamp": ts,
                    "hs_call_title": p.title(),
                    "hs_call_body": p.text(),
                    "hs_call_direction": "INBOUND",
                    "hs_call_status": "COMPLETED",
                    "hs_call_duration": int((p.duration_s or 0) * 1000),
                    "hs_call_from_number": p.caller_phone,
                    "hs_call_recording_url": p.recording_url,
                },
                "associations": [
                    {
                        "to": {"id": cid},
                        "types": [
                            {"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 194}
                        ],
                    }
                ],
            },
        )
        r.raise_for_status()
        return f"contact:{cid} call:{r.json().get('id')}"


class SalesforceBackend:
    """Connected App, OAuth 2.0 client-credentials flow (secret = consumer secret)."""

    provider = Provider.SALESFORCE
    API = "v60.0"

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def _token(self, c: Connector, secret: str | None) -> tuple[str, str]:
        base = _need(c.options.get("instance_url"), "instance_url").rstrip("/")
        r = await self._http.post(
            f"{base}/services/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "client_id": _need(c.options.get("client_id"), "client_id"),
                "client_secret": _need(secret, "consumer secret"),
            },
        )
        r.raise_for_status()
        j = r.json()
        return str(j["access_token"]), str(j.get("instance_url") or base)

    async def test(self, c: Connector, secret: str | None) -> str:
        tok, inst = await self._token(c, secret)
        r = await self._http.get(f"{inst}/services/data/{self.API}/limits", headers=_auth(tok))
        r.raise_for_status()
        return inst.removeprefix("https://")

    async def _find(self, tok: str, inst: str, phone: str | None) -> tuple[str, str] | None:
        if not phone:
            return None
        digits = phone.lstrip("+")
        soql = (
            "FIND {"
            + digits
            + "} IN PHONE FIELDS RETURNING Contact(Id), Lead(Id WHERE IsConverted=false)"
        )
        r = await self._http.get(
            f"{inst}/services/data/{self.API}/search/", headers=_auth(tok), params={"q": soql}
        )
        r.raise_for_status()
        for rec in r.json().get("searchRecords", []):
            return str(rec["attributes"]["type"]), str(rec["Id"])
        return None

    async def push(self, c: Connector, secret: str | None, p: Payload) -> str | None:
        tok, inst = await self._token(c, secret)
        who = await self._find(tok, inst, p.caller_phone)
        base = f"{inst}/services/data/{self.API}/sobjects"
        if who is None:
            first, _, last = (p.caller_name or "").partition(" ")
            body: dict[str, Any] = {
                "FirstName": first or None,
                "LastName": last or first or "Unknown caller",
                "Company": p.business_name or "Unknown",
                "Phone": p.caller_phone,
                "Email": p.caller_email,
                "LeadSource": "Phone Inquiry",
                "Description": p.text(),
            }
            body |= p.mapped(c.field_map) if c.field_map else {}
            r = await self._http.post(
                f"{base}/Lead", headers=_auth(tok), json={k: v for k, v in body.items() if v}
            )
            r.raise_for_status()
            who = ("Lead", str(r.json()["id"]))
        r = await self._http.post(
            f"{base}/Task",
            headers=_auth(tok),
            json={
                "Subject": p.title(),
                "Description": p.text(),
                "Status": "Completed",
                "TaskSubtype": "Call",
                "CallType": "Inbound",
                "CallDurationInSeconds": int(p.duration_s or 0),
                "ActivityDate": p.occurred_at.date().isoformat(),
                "WhoId": who[1],
            },
        )
        r.raise_for_status()
        return f"{who[0]}:{who[1]} task:{r.json().get('id')}"


class PipedriveBackend:
    provider = Provider.PIPEDRIVE

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    def _base(self, c: Connector) -> str:
        dom = _need(c.options.get("company_domain"), "company_domain")
        return f"https://{dom}.pipedrive.com/api/v1"

    async def test(self, c: Connector, secret: str | None) -> str:
        r = await self._http.get(
            f"{self._base(c)}/users/me", params={"api_token": _need(secret, "API token")}
        )
        r.raise_for_status()
        d = r.json().get("data", {})
        return f"{d.get('name')} @ {d.get('company_name')}"

    async def push(self, c: Connector, secret: str | None, p: Payload) -> str | None:
        base, tok = self._base(c), {"api_token": _need(secret, "API token")}
        secs = int(p.duration_s or 0)
        duration = f"{secs // 3600:02d}:{secs % 3600 // 60:02d}"
        pid: int | None = None
        if p.caller_phone:
            r = await self._http.get(
                f"{base}/persons/search",
                params=tok | {"term": p.caller_phone, "fields": "phone", "limit": 1},
            )
            r.raise_for_status()
            items = (r.json().get("data") or {}).get("items") or []
            if items:
                pid = int(items[0]["item"]["id"])
        if pid is None:
            body: dict[str, Any] = {
                "name": p.caller_name or p.caller_phone or "Unknown caller",
                "phone": [p.caller_phone] if p.caller_phone else [],
                "email": [p.caller_email] if p.caller_email else [],
            }
            body |= p.mapped(c.field_map) if c.field_map else {}
            r = await self._http.post(f"{base}/persons", params=tok, json=body)
            r.raise_for_status()
            pid = int(r.json()["data"]["id"])
        r = await self._http.post(
            f"{base}/activities",
            params=tok,
            json={
                "subject": p.title(),
                "type": "call",
                "done": 1,
                "due_date": p.occurred_at.date().isoformat(),
                "duration": duration,
                "person_id": pid,
                "note": p.text().replace("\n", "<br>"),
            },
        )
        r.raise_for_status()
        return f"person:{pid} activity:{r.json()['data']['id']}"


class ZohoBackend:
    """Self-client refresh token (secret); client id in options, client secret in options too."""

    provider = Provider.ZOHO

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def _token(self, c: Connector, secret: str | None) -> tuple[str, str]:
        acc = c.options.get("accounts_domain", "accounts.zoho.eu")
        api = "www.zohoapis." + acc.removeprefix("accounts.").removeprefix("zoho.")
        r = await self._http.post(
            f"https://{acc}/oauth/v2/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": _need(secret, "refresh token"),
                "client_id": _need(c.options.get("client_id"), "client_id"),
                "client_secret": _need(c.options.get("client_secret"), "client_secret"),
            },
        )
        r.raise_for_status()
        j = r.json()
        if "access_token" not in j:
            raise RuntimeError(f"Zoho token error: {j.get('error', 'unknown')}")
        return str(j["access_token"]), f"https://{api}/crm/v6"

    async def test(self, c: Connector, secret: str | None) -> str:
        tok, api = await self._token(c, secret)
        r = await self._http.get(f"{api}/org", headers={"Authorization": f"Zoho-oauthtoken {tok}"})
        r.raise_for_status()
        org = (r.json().get("org") or [{}])[0]
        return str(org.get("company_name") or "Zoho CRM")

    async def push(self, c: Connector, secret: str | None, p: Payload) -> str | None:
        tok, api = await self._token(c, secret)
        h = {"Authorization": f"Zoho-oauthtoken {tok}"}
        lead_id: str | None = None
        if p.caller_phone:
            r = await self._http.get(
                f"{api}/Leads/search", headers=h, params={"phone": p.caller_phone}
            )
            if r.status_code == 200:
                data = r.json().get("data") or []
                if data:
                    lead_id = str(data[0]["id"])
        if lead_id is None:
            first, _, last = (p.caller_name or "").partition(" ")
            body: dict[str, Any] = {
                "First_Name": first or None,
                "Last_Name": last or first or "Unknown caller",
                "Company": p.business_name or "Unknown",
                "Phone": p.caller_phone,
                "Email": p.caller_email,
                "Lead_Source": "Phone",
                "Description": p.text(),
            }
            body |= p.mapped(c.field_map) if c.field_map else {}
            r = await self._http.post(
                f"{api}/Leads", headers=h, json={"data": [{k: v for k, v in body.items() if v}]}
            )
            r.raise_for_status()
            lead_id = str(r.json()["data"][0]["details"]["id"])
        r = await self._http.post(
            f"{api}/Notes",
            headers=h,
            json={
                "data": [
                    {
                        "Note_Title": p.title(),
                        "Note_Content": p.text(),
                        "Parent_Id": {"module": {"api_name": "Leads"}, "id": lead_id},
                    }
                ]
            },
        )
        r.raise_for_status()
        return f"lead:{lead_id}"


class ServiceM8Backend:
    """Trades reference adapter: upsert client (company) + create a quote-stage job."""

    provider = Provider.SERVICEM8
    BASE = "https://api.servicem8.com/api_1.0"

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    @staticmethod
    def _h(secret: str | None) -> dict[str, str]:
        return {"X-Api-Key": _need(secret, "API key"), "accept": "application/json"}

    async def test(self, c: Connector, secret: str | None) -> str:
        r = await self._http.get(f"{self.BASE}/vendor.json", headers=self._h(secret))
        r.raise_for_status()
        j = r.json()
        return str((j[0] if isinstance(j, list) and j else j).get("name") or "ServiceM8")

    async def push(self, c: Connector, secret: str | None, p: Payload) -> str | None:
        h = self._h(secret)
        name = p.caller_name or p.caller_phone or "Unknown caller"
        r = await self._http.get(
            f"{self.BASE}/company.json",
            headers=h,
            params={"$filter": f"name eq '{name.replace(chr(39), '')}'"},
        )
        r.raise_for_status()
        found = r.json()
        if found:
            company_uuid = str(found[0]["uuid"])
        else:
            r = await self._http.post(f"{self.BASE}/company.json", headers=h, json={"name": name})
            r.raise_for_status()
            company_uuid = r.headers.get("x-record-uuid", "")
            if p.caller_phone or p.caller_email:
                await self._http.post(
                    f"{self.BASE}/companycontact.json",
                    headers=h,
                    json={
                        "company_uuid": company_uuid,
                        "first": (p.caller_name or "").split(" ")[0] or "Caller",
                        "last": " ".join((p.caller_name or "").split(" ")[1:]),
                        "mobile": p.caller_phone or "",
                        "email": p.caller_email or "",
                        "type": "JOB",
                    },
                )
        r = await self._http.post(
            f"{self.BASE}/job.json",
            headers=h,
            json={
                "company_uuid": company_uuid,
                "status": c.options.get("job_status", "Quote"),
                "job_description": p.text(),
                "job_address": p.extracted.get("address", ""),
            },
        )
        r.raise_for_status()
        return f"client:{company_uuid} job:{r.headers.get('x-record-uuid', '')}"


class GoogleSheetsBackend:
    """OAuth (same Google app as Calendar) + Sheets values.append; one row per event."""

    provider = Provider.GOOGLE_SHEETS
    AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN = "https://oauth2.googleapis.com/token"
    SCOPES = "https://www.googleapis.com/auth/spreadsheets https://www.googleapis.com/auth/userinfo.email"

    def __init__(self, client_id: str, client_secret: str, http: httpx.AsyncClient) -> None:
        self._id, self._secret, self._http = client_id, client_secret, http

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
        me = await self._http.get(
            "https://www.googleapis.com/oauth2/v2/userinfo", headers=_auth(tok["access_token"])
        )
        if me.is_success:
            email = me.json().get("email")
        return OAuthTokens(refresh_token=tok["refresh_token"], account_email=email)

    async def _access(self, secret: str | None) -> str:
        r = await self._http.post(
            self.TOKEN,
            data={
                "refresh_token": _need(secret, "Google authorisation"),
                "client_id": self._id,
                "client_secret": self._secret,
                "grant_type": "refresh_token",
            },
        )
        r.raise_for_status()
        return str(r.json()["access_token"])

    def _range(self, c: Connector) -> tuple[str, str]:
        sid = _need(c.options.get("spreadsheet_id"), "spreadsheet_id")
        return sid, c.options.get("sheet") or "Parlio"

    async def test(self, c: Connector, secret: str | None) -> str:
        tok = await self._access(secret)
        sid, _ = self._range(c)
        r = await self._http.get(
            f"https://sheets.googleapis.com/v4/spreadsheets/{sid}",
            headers=_auth(tok),
            params={"fields": "properties.title"},
        )
        r.raise_for_status()
        return str(r.json()["properties"]["title"])

    async def push(self, c: Connector, secret: str | None, p: Payload) -> str | None:
        tok = await self._access(secret)
        sid, sheet = self._range(c)
        row = p.mapped(c.field_map) if c.field_map else {k: p.flat().get(k) for k in PAYLOAD_FIELDS}
        r = await self._http.post(
            f"https://sheets.googleapis.com/v4/spreadsheets/{sid}/values/{sheet}!A1:append",
            headers=_auth(tok),
            params={"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"},
            json={"values": [["" if v is None else str(v) for v in row.values()]]},
        )
        r.raise_for_status()
        return str(r.json().get("updates", {}).get("updatedRange"))


# -- service -----------------------------------------------------------------------------------


class ConnectorService:
    def __init__(
        self,
        store: CallStore,
        vault: Vault,
        backends: dict[Provider, ConnectorBackend],
        *,
        public_url: str = "http://localhost:8000",
    ) -> None:
        self.store = store
        self.vault = vault
        self.backends = backends
        self.public_url = public_url

    # connectors
    async def list_all(self, tenant_id: str) -> list[Connector]:
        docs = await self.store.list_docs(CONN_KIND, tenant_id)
        return sorted((Connector.model_validate(d.data) for d in docs), key=lambda c: c.created_at)

    async def get(self, tenant_id: str, cid: str) -> Connector | None:
        d = await self.store.get_doc(CONN_KIND, cid)
        if d is None or d.tenant_id != tenant_id:
            return None
        return Connector.model_validate(d.data)

    async def put(self, c: Connector, *, secret: str | None = None) -> Connector:
        if secret:
            c.secret_sealed = self.vault.seal(secret)
        info = PROVIDER_INFO[c.provider]
        if info.auth == "url" and c.target_url and not c.signing_secret_sealed:
            c.signing_secret_sealed = self.vault.seal(secrets.token_urlsafe(24))
        if not c.name:
            c.name = info.label
        await self.store.put_doc(c.to_doc())
        return c

    async def delete(self, tenant_id: str, cid: str) -> bool:
        if await self.get(tenant_id, cid) is None:
            return False
        return await self.store.delete_doc(CONN_KIND, cid)

    def signing_secret(self, c: Connector) -> str | None:
        return self.vault.open(c.signing_secret_sealed) if c.signing_secret_sealed else None

    def _secret(self, c: Connector) -> str | None:
        if c.secret_sealed:
            return self.vault.open(c.secret_sealed)
        return self.signing_secret(c)

    def backend(self, c: Connector) -> ConnectorBackend:
        be = self.backends.get(c.provider)
        if be is None and c.provider in (Provider.ZAPIER, Provider.MAKE):
            be = self.backends.get(Provider.WEBHOOK)
        if be is None:
            raise RuntimeError(f"{c.provider} is not enabled on this server")
        return be

    async def _mark(self, c: Connector, ok: bool, detail: str | None) -> None:
        c.last_sync_at = datetime.now(UTC)
        c.last_error = None if ok else detail
        c.status = "connected" if ok else "error"
        await self.store.put_doc(c.to_doc())

    async def test(self, c: Connector) -> tuple[bool, str]:
        try:
            label = await self.backend(c).test(c, self._secret(c))
        except Exception as e:
            await self._mark(c, False, str(e)[:300])
            return False, str(e)[:300]
        c.account_label = label
        await self._mark(c, True, None)
        return True, label

    # OAuth (Google Sheets today; HubSpot/Zoho apps later)
    def oauth_backend(self, provider: Provider) -> OAuthConnectorBackend | None:
        be = self.backends.get(provider)
        return be if isinstance(be, GoogleSheetsBackend) else None

    async def oauth_start(self, c: Connector, redirect_uri: str) -> str:
        be = self.oauth_backend(c.provider)
        if be is None:
            raise ValueError(f"{c.provider} OAuth is not configured on this server")
        await self.put(c)
        state = secrets.token_urlsafe(24)
        await self.store.put_doc(
            TenantDoc(
                kind=STATE_KIND,
                id=state,
                tenant_id=c.tenant_id,
                data={"connector_id": c.id, "redirect_uri": redirect_uri},
            )
        )
        return be.auth_url(state, redirect_uri)

    async def oauth_callback(self, state: str, code: str) -> Connector:
        st = await self.store.get_doc(STATE_KIND, state)
        if st is None:
            raise ValueError("unknown or expired OAuth state")
        await self.store.delete_doc(STATE_KIND, state)
        c = await self.get(st.tenant_id, st.data["connector_id"])
        if c is None:
            raise ValueError("connector no longer exists")
        be = self.oauth_backend(c.provider)
        if be is None:
            raise ValueError(f"{c.provider} OAuth is not configured on this server")
        tokens = await be.exchange_code(code, st.data["redirect_uri"])
        c.account_label = tokens.account_email
        await self.put(c, secret=tokens.refresh_token)
        await self.test(c)
        return c

    # jobs
    async def jobs(self, tenant_id: str, limit: int = 100) -> list[SyncJob]:
        docs = await self.store.list_docs(JOB_KIND, tenant_id, limit)
        return [SyncJob.model_validate(d.data) for d in docs]

    async def get_job(self, tenant_id: str, job_id: str) -> SyncJob | None:
        d = await self.store.get_doc(JOB_KIND, job_id)
        if d is None or d.tenant_id != tenant_id:
            return None
        return SyncJob.model_validate(d.data)

    async def dispatch(self, p: Payload) -> list[SyncJob]:
        out: list[SyncJob] = []
        for c in await self.list_all(p.tenant_id):
            if not c.wants(p):
                continue
            job = SyncJob(
                tenant_id=p.tenant_id,
                connector_id=c.id,
                provider=c.provider,
                event=p.event,
                payload=p.model_dump(mode="json"),
            )
            out.append(await self.run_job(c, job))
        return out

    async def run_job(self, c: Connector, job: SyncJob) -> SyncJob:
        job.attempts += 1
        job.updated_at = datetime.now(UTC)
        try:
            job.external_ref = await self.backend(c).push(
                c, self._secret(c), Payload.model_validate(job.payload)
            )
            job.status, job.error, job.next_attempt_at = JobStatus.SENT, None, None
            await self._mark(c, True, None)
        except Exception as e:
            job.error = str(e)[:300]
            if job.attempts >= MAX_ATTEMPTS:
                job.status, job.next_attempt_at = JobStatus.FAILED, None
            else:
                job.status = JobStatus.RETRY
                job.next_attempt_at = job.updated_at + timedelta(
                    seconds=BACKOFF_S[min(job.attempts - 1, len(BACKOFF_S) - 1)]
                )
            log.warning("connector %s (%s) push failed: %s", c.id, c.provider, job.error)
            await self._mark(c, False, job.error)
        await self.store.put_doc(job.to_doc())
        return job

    async def retry(self, tenant_id: str, job_id: str, *, force: bool = False) -> SyncJob | None:
        job = await self.get_job(tenant_id, job_id)
        if job is None:
            return None
        c = await self.get(tenant_id, job.connector_id)
        if c is None:
            job.status, job.error = JobStatus.SKIPPED, "connector deleted"
            await self.store.put_doc(job.to_doc())
            return job
        if force:
            job.attempts = 0
        return await self.run_job(c, job)

    async def retry_due(self, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        n = 0
        for d in await self.store.list_docs(JOB_KIND, None, 500):
            job = SyncJob.model_validate(d.data)
            if job.status != JobStatus.RETRY or (job.next_attempt_at or now) > now:
                continue
            c = await self.get(job.tenant_id, job.connector_id)
            if c is None or not c.enabled:
                job.status, job.error = JobStatus.SKIPPED, "connector disabled or deleted"
                await self.store.put_doc(job.to_doc())
                continue
            await self.run_job(c, job)
            n += 1
        return n

    # inbound API keys
    async def api_keys(self, tenant_id: str) -> list[TenantApiKey]:
        docs = await self.store.list_docs(APIKEY_KIND, tenant_id)
        return sorted(
            (TenantApiKey.model_validate(d.data) for d in docs), key=lambda k: k.created_at
        )

    async def create_api_key(self, tenant_id: str, name: str) -> tuple[TenantApiKey, str]:
        raw = f"pk_{secrets.token_urlsafe(32)}"
        k = TenantApiKey(tenant_id=tenant_id, name=name, prefix=raw[:10], key_hash=hash_key(raw))
        await self.store.put_doc(k.to_doc())
        return k, raw

    async def revoke_api_key(self, tenant_id: str, key_id: str) -> bool:
        d = await self.store.get_doc(APIKEY_KIND, key_id)
        if d is None or d.tenant_id != tenant_id:
            return False
        k = TenantApiKey.model_validate(d.data)
        k.revoked = True
        await self.store.put_doc(k.to_doc())
        return True

    async def resolve_api_key(self, raw: str) -> TenantApiKey | None:
        h = hash_key(raw)
        for d in await self.store.list_docs(APIKEY_KIND, None, 1000):
            k = TenantApiKey.model_validate(d.data)
            if not k.revoked and hmac.compare_digest(k.key_hash, h):
                k.last_used_at = datetime.now(UTC)
                await self.store.put_doc(k.to_doc())
                return k
        return None


class RetryLoop:
    def __init__(self, svc: ConnectorService, interval_s: float = 60.0) -> None:
        self.svc, self.interval_s = svc, interval_s
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.interval_s)
            try:
                await self.svc.retry_due()
            except Exception:
                log.warning("connector retry sweep failed", exc_info=True)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task


# -- CSV export --------------------------------------------------------------------------------


def calls_csv(calls: list[CallRecord]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        [
            "call_id",
            "started_at",
            "caller",
            "caller_name",
            "caller_type",
            "kind",
            "duration_s",
            "summary",
            "reason",
            "email",
            "ticket_ids",
            "recording",
        ]
    )
    for c in calls:
        w.writerow(
            [
                c.call_id,
                c.started_at.isoformat(),
                c.caller or "",
                c.extracted.get("name", ""),
                c.caller_type or "",
                c.kind,
                int(c.duration_s or 0),
                c.summary or "",
                c.extracted.get("reason", ""),
                c.extracted.get("email", ""),
                " ".join(c.ticket_ids),
                c.recordings[0] if c.recordings else "",
            ]
        )
    return buf.getvalue()


def contacts_csv(contacts: list[Contact]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        ["id", "phone", "name", "email", "status", "vip", "calls", "first_seen", "last_seen"]
    )
    for c in contacts:
        w.writerow(
            [
                c.id,
                c.e164,
                c.name or "",
                c.email or "",
                c.status,
                c.vip,
                c.call_count,
                c.first_seen_at.isoformat(),
                c.last_seen_at.isoformat(),
            ]
        )
    return buf.getvalue()


def tickets_csv(tickets: list[Ticket]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        [
            "id",
            "created_at",
            "status",
            "priority",
            "department",
            "caller_name",
            "caller_number",
            "reason",
            "assigned_to",
        ]
    )
    for t in tickets:
        w.writerow(
            [
                t.id,
                t.created_at.isoformat(),
                t.status,
                t.priority,
                t.department or "",
                t.caller_name or "",
                t.caller_number or "",
                t.reason,
                t.assigned_to or "",
            ]
        )
    return buf.getvalue()


def build_backends(
    http: httpx.AsyncClient,
    *,
    google_client_id: str | None = None,
    google_client_secret: str | None = None,
) -> dict[Provider, ConnectorBackend]:
    out: dict[Provider, ConnectorBackend] = {
        Provider.SIMULATED: SimulatedBackend(),
        Provider.WEBHOOK: WebhookBackend(http),
        Provider.ZAPIER: WebhookBackend(http, Provider.ZAPIER),
        Provider.MAKE: WebhookBackend(http, Provider.MAKE),
        Provider.TEAMS: TeamsBackend(http),
        Provider.HUBSPOT: HubSpotBackend(http),
        Provider.SALESFORCE: SalesforceBackend(http),
        Provider.PIPEDRIVE: PipedriveBackend(http),
        Provider.ZOHO: ZohoBackend(http),
        Provider.SERVICEM8: ServiceM8Backend(http),
    }
    if google_client_id and google_client_secret:
        out[Provider.GOOGLE_SHEETS] = GoogleSheetsBackend(
            google_client_id, google_client_secret, http
        )
    return out
