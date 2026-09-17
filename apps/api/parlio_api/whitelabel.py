"""Phase 14 white-label / reseller mode and the compliance pack.

`Branding` is a per-tenant document (name, logo, colours, custom dashboard domain, support
contact, hide "powered by ParlioTec"). The dashboard fetches `/v1/public/branding?host=` to theme
itself by the domain it was loaded on; custom domains are verified with a DNS TXT record before
they resolve. An *agency* tenant can create child (client) tenants: the child inherits the
agency's branding unless overridden, the agency's admins get an admin membership on the child,
and billing stays per client (each child has its own subscription) with a roll-up view for the
agency.

`compliance_pack()` assembles everything a customer's DPO asks for in one place: consent
announcement, retention & redaction policy, region profile per assistant, sub-processors actually
in use, security policy summary, and an audit export.
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import re
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from parlio_api.billing import BillingService, UsageSummary
from parlio_api.compliance import ComplianceService, RetentionPolicy
from parlio_api.observability import AuditEntry, AuditLog
from parlio_api.security import SecurityPolicy, SecurityService
from parlio_api.store import CallStore, Member, TenantDoc
from parlio_voice.models import (
    AssistantConfig,
    LLMProvider,
    RegionProfile,
    STTProvider,
    TTSProvider,
)

log = logging.getLogger("parlio.api.whitelabel")

BRANDING_KIND = "branding"
LINK_KIND = "tenant_link"
_HEX = r"^#[0-9a-fA-F]{6}$"
_HOST = re.compile(r"^(?=.{4,253}$)([a-z0-9-]+\.)+[a-z]{2,}$")


class Branding(BaseModel):
    tenant_id: str
    brand_name: str = Field("ParlioTec", min_length=1, max_length=60)
    logo_url: str | None = None
    icon_url: str | None = None
    primary_colour: str = Field("#3b6cf6", pattern=_HEX)
    accent_colour: str = Field("#7c5cff", pattern=_HEX)
    support_email: str | None = None
    support_url: str | None = None
    hide_powered_by: bool = False
    custom_domain: str | None = None
    domain_verified: bool = False
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def to_doc(self) -> TenantDoc:
        return TenantDoc(
            kind=BRANDING_KIND,
            id=self.tenant_id,
            tenant_id=self.tenant_id,
            data=self.model_dump(mode="json"),
        )


class PublicBranding(BaseModel):
    brand_name: str
    logo_url: str | None
    icon_url: str | None
    primary_colour: str
    accent_colour: str
    support_email: str | None
    support_url: str | None
    hide_powered_by: bool
    tenant_id: str | None = None


class DomainInstructions(BaseModel):
    domain: str
    cname_target: str
    txt_name: str
    txt_value: str
    verified: bool


class TenantLink(BaseModel):
    """agency (parent) -> client (child) relationship; stored under the *parent* tenant."""

    id: str = Field(default_factory=lambda: f"tl-{uuid4().hex[:8]}")
    parent_tenant_id: str
    child_tenant_id: str
    client_name: str
    inherit_branding: bool = True
    notes: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def to_doc(self) -> TenantDoc:
        return TenantDoc(
            kind=LINK_KIND,
            id=self.id,
            tenant_id=self.parent_tenant_id,
            data=self.model_dump(mode="json"),
            created_at=self.created_at,
        )


class ClientSummary(BaseModel):
    link: TenantLink
    usage: UsageSummary | None = None
    members: int = 0
    assistants: int = 0


class WhiteLabelService:
    def __init__(
        self,
        store: CallStore,
        billing: BillingService | None,
        *,
        dashboard_host: str,
        verify_salt: str,
    ) -> None:
        self.store = store
        self.billing = billing
        self.dashboard_host = dashboard_host
        self._salt = verify_salt

    # branding
    async def branding(self, tenant_id: str) -> Branding:
        doc = await self.store.get_doc(BRANDING_KIND, tenant_id)
        if doc is not None:
            return Branding.model_validate(doc.data)
        parent = await self.parent_of(tenant_id)
        if parent is not None and parent.inherit_branding:
            pb = await self.branding(parent.parent_tenant_id)
            return pb.model_copy(
                update={"tenant_id": tenant_id, "custom_domain": None, "domain_verified": False}
            )
        return Branding(tenant_id=tenant_id)

    async def save_branding(self, b: Branding) -> Branding:
        prev = await self.store.get_doc(BRANDING_KIND, b.tenant_id)
        prev_domain = prev.data.get("custom_domain") if prev else None
        if b.custom_domain:
            b.custom_domain = b.custom_domain.strip().lower()
            if not _HOST.match(b.custom_domain):
                raise ValueError("custom domain must be a hostname like app.youragency.co.uk")
            other = await self._tenant_for_host(b.custom_domain)
            if other and other != b.tenant_id:
                raise ValueError("that domain is already claimed by another organisation")
        if b.custom_domain != prev_domain:
            b.domain_verified = False
        elif prev is not None:
            b.domain_verified = bool(prev.data.get("domain_verified"))
        b.updated_at = datetime.now(UTC)
        await self.store.put_doc(b.to_doc())
        return b

    def _txt_value(self, tenant_id: str, domain: str) -> str:
        return (
            "parlio-verify="
            + hashlib.sha256(f"{self._salt}:{tenant_id}:{domain}".encode()).hexdigest()[:24]
        )

    async def domain_instructions(self, tenant_id: str) -> DomainInstructions | None:
        b = await self.branding(tenant_id)
        if not b.custom_domain:
            return None
        return DomainInstructions(
            domain=b.custom_domain,
            cname_target=self.dashboard_host,
            txt_name=f"_parlio.{b.custom_domain}",
            txt_value=self._txt_value(tenant_id, b.custom_domain),
            verified=b.domain_verified,
        )

    async def verify_domain(self, tenant_id: str, txt_records: list[str]) -> Branding:
        """`txt_records` are the TXT values found at _parlio.<domain> (resolved by the caller
        so this stays testable offline)."""
        b = await self.branding(tenant_id)
        if not b.custom_domain:
            raise ValueError("no custom domain set")
        expected = self._txt_value(tenant_id, b.custom_domain)
        b.domain_verified = any(r.strip().strip('"') == expected for r in txt_records)
        await self.store.put_doc(b.to_doc())
        return b

    async def _tenant_for_host(self, host: str) -> str | None:
        for d in await self.store.list_docs(BRANDING_KIND, None, 2000):
            if d.data.get("custom_domain") == host:
                return d.tenant_id
        return None

    async def public_branding(self, host: str | None) -> PublicBranding:
        host = (host or "").split(":")[0].lower()
        tenant_id = await self._tenant_for_host(host) if host else None
        if tenant_id is None:
            return PublicBranding(
                **Branding(tenant_id="").model_dump(
                    exclude={"tenant_id", "custom_domain", "domain_verified", "updated_at"}
                )
            )
        b = await self.branding(tenant_id)
        if not b.domain_verified:
            return PublicBranding(
                **Branding(tenant_id="").model_dump(
                    exclude={"tenant_id", "custom_domain", "domain_verified", "updated_at"}
                )
            )
        return PublicBranding(
            tenant_id=tenant_id,
            **b.model_dump(exclude={"tenant_id", "custom_domain", "domain_verified", "updated_at"}),
        )

    # agency / clients
    async def clients(self, parent_tenant_id: str) -> list[TenantLink]:
        docs = await self.store.list_docs(LINK_KIND, parent_tenant_id, 500)
        return sorted(
            (TenantLink.model_validate(d.data) for d in docs), key=lambda x: x.client_name
        )

    async def parent_of(self, child_tenant_id: str) -> TenantLink | None:
        for d in await self.store.list_docs(LINK_KIND, None, 5000):
            if d.data.get("child_tenant_id") == child_tenant_id:
                return TenantLink.model_validate(d.data)
        return None

    async def create_client(
        self,
        parent_tenant_id: str,
        *,
        client_name: str,
        business_name: str,
        admins: list[Member],
        inherit_branding: bool = True,
    ) -> TenantLink:
        if await self.parent_of(parent_tenant_id) is not None:
            raise ValueError("a client organisation cannot itself have clients")
        slug = re.sub(r"[^a-z0-9]+", "-", client_name.lower()).strip("-")[:24] or "client"
        child_id = f"{slug}-{uuid4().hex[:6]}"
        cfg = AssistantConfig(
            tenant_id=child_id,
            company_id=f"{child_id}-co",
            assistant_id=f"{child_id}-assistant",
            business_name=business_name,
        )
        await self.store.upsert_assistant(cfg, [])
        for m in admins:
            await self.store.upsert_member(
                m.model_copy(update={"tenant_id": child_id, "role": "admin", "status": "active"})
            )
        link = TenantLink(
            parent_tenant_id=parent_tenant_id,
            child_tenant_id=child_id,
            client_name=client_name,
            inherit_branding=inherit_branding,
        )
        await self.store.put_doc(link.to_doc())
        return link

    async def update_client(
        self, parent_tenant_id: str, link_id: str, **changes: Any
    ) -> TenantLink | None:
        doc = await self.store.get_doc(LINK_KIND, link_id)
        if doc is None or doc.tenant_id != parent_tenant_id:
            return None
        link = TenantLink.model_validate(doc.data).model_copy(update=changes)
        await self.store.put_doc(link.to_doc())
        return link

    async def client_summaries(self, parent_tenant_id: str) -> list[ClientSummary]:
        out: list[ClientSummary] = []
        for link in await self.clients(parent_tenant_id):
            usage = None
            if self.billing is not None:
                try:
                    usage = await self.billing.usage(link.child_tenant_id)
                except Exception:
                    log.warning("usage roll-up failed for %s", link.child_tenant_id, exc_info=True)
            out.append(
                ClientSummary(
                    link=link,
                    usage=usage,
                    members=len(await self.store.list_members(link.child_tenant_id)),
                    assistants=len(await self.store.list_assistants(link.child_tenant_id)),
                )
            )
        return out


# -- compliance pack -------------------------------------------------------------------------------

SUB_PROCESSORS: dict[str, dict[str, str]] = {
    STTProvider.DEEPGRAM: {
        "name": "Deepgram",
        "purpose": "speech-to-text",
        "region": "US (EU endpoint available)",
        "dpa": "yes",
    },
    LLMProvider.OPENAI: {
        "name": "OpenAI",
        "purpose": "conversation / summaries / QA",
        "region": "US (zero data retention API)",
        "dpa": "yes",
    },
    TTSProvider.CARTESIA: {
        "name": "Cartesia",
        "purpose": "text-to-speech",
        "region": "US",
        "dpa": "yes",
    },
    "telnyx": {
        "name": "Telnyx",
        "purpose": "UK numbers, SIP, SMS",
        "region": "UK/EU PoPs",
        "dpa": "yes",
    },
    "digitalocean": {
        "name": "DigitalOcean (London)",
        "purpose": "API, database, media, LiveKit",
        "region": "UK",
        "dpa": "yes",
    },
    "cloudflare": {
        "name": "Cloudflare",
        "purpose": "dashboard hosting / TLS",
        "region": "global edge",
        "dpa": "yes",
    },
    "supabase": {
        "name": "Supabase",
        "purpose": "dashboard sign-in",
        "region": "EU (London)",
        "dpa": "yes",
    },
}


class AssistantRegion(BaseModel):
    assistant_id: str
    name: str
    region_profile: RegionProfile
    stt: list[str]
    llm: list[str]
    tts: list[str]
    consent_announcement: bool
    recording_enabled: bool


class CompliancePack(BaseModel):
    tenant_id: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    data_residency: str = "UK (DigitalOcean London) for call records, transcripts, recordings"
    assistants: list[AssistantRegion]
    retention: RetentionPolicy
    security: SecurityPolicy
    sub_processors: list[dict[str, str]]
    consent: dict[str, Any]
    audit_entries: int
    notes: list[str] = Field(default_factory=list)


async def compliance_pack(
    tenant_id: str,
    store: CallStore,
    compliance: ComplianceService,
    security: SecurityService,
    audit: AuditLog,
) -> CompliancePack:
    cfgs = await store.list_assistants(tenant_id)
    assistants = [
        AssistantRegion(
            assistant_id=c.assistant_id,
            name=c.name,
            region_profile=c.region_profile,
            stt=[str(p) for p in c.providers.stt],
            llm=[str(p) for p in c.providers.llm],
            tts=[str(p) for p in c.providers.tts],
            consent_announcement=bool(c.recording.consent_announcement),
            recording_enabled=c.recording.enabled,
        )
        for c in cfgs
    ]
    used: set[str] = {"telnyx", "digitalocean", "cloudflare", "supabase"}
    for c in cfgs:
        used.update(str(p) for p in c.providers.stt + c.providers.llm + c.providers.tts)
    subs = [SUB_PROCESSORS[k] for k in SUB_PROCESSORS if k in used]
    notes: list[str] = []
    if any(c.region_profile != RegionProfile.STANDARD for c in cfgs):
        notes.append(
            "Sovereign profile is pinned on at least one assistant. UK-only model providers are "
            "delivered in Phase 15/16; until then STT/LLM/TTS still use the listed US vendors."
        )
    entries = await audit.recent(tenant_id, 1000)
    return CompliancePack(
        tenant_id=tenant_id,
        assistants=assistants,
        retention=await compliance.policy(tenant_id),
        security=await security.policy(tenant_id),
        sub_processors=subs,
        consent={
            "call_recording_announced": all(bool(c.recording.consent_announcement) for c in cfgs)
            if cfgs
            else False,
            "sms_marketing": "explicit opt-in only (Phase 12 payment links / Phase 8b marketing)",
            "voice_cloning": "explicit owner consent statement recorded per clone",
            "lawful_basis": "legitimate interests (answering business calls); "
            "consent for marketing",
        },
        audit_entries=len(entries),
        notes=notes,
    )


def audit_csv(entries: list[AuditEntry]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["at", "actor", "action", "target", "method", "path", "status", "ip"])
    for e in entries:
        w.writerow(
            [
                e.at.isoformat(),
                e.actor,
                e.action,
                e.target or "",
                e.method or "",
                e.path or "",
                e.status or "",
                e.ip or "",
            ]
        )
    return buf.getvalue()
