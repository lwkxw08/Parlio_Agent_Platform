"""BYO SIP trunking (Phase 5b): let a customer connect their PBX or SIP account to Parlio.

Three connection modes, one `SipTrunk` record:
- `forward`      : customer forwards their number to a ParlioTec (carrier) number. Nothing to
                   provision; the trunk just documents the numbers and routing.
- `pbx`          : customer's PBX (3CX, FreePBX, RingCentral...) sends calls to ParlioTec with
                   ParlioTec-issued digest credentials (+ optional IP allowlist). Provisioned as a
                   LiveKit inbound trunk with auth; extension transfers go back over a LiveKit
                   outbound trunk to the PBX.
- `byo_register` : ParlioTec registers to the customer's SIP provider (Voipfone, Sipgate...) with
                   the customer's credentials, so their existing number rings ParlioTec. Needs a
                   registering SIP edge (Kamailio/OpenSIPS) in front of LiveKit SIP, which is a
                   deployment concern reported through `Registrar`; the data model, credential
                   handling and routing are complete here.

Passwords are sealed in the vault and never returned after creation (ParlioTec-issued ones are shown
once, like API keys). `SipProvisioner` is the seam to LiveKit; `SimulatedProvisioner` keeps the
whole flow testable without a live SIP edge or approved carrier number.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import secrets
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from parlio_api.store import CallStore, TenantDoc
from parlio_api.vault import Vault
from parlio_voice.models import Schedule

log = logging.getLogger("parlio.api.sip")

KIND = "sip_trunk"
E164 = re.compile(r"^\+[1-9]\d{6,14}$")
HOST = re.compile(r"^[a-zA-Z0-9.-]+(:\d{2,5})?$")


class TrunkMode(StrEnum):
    FORWARD = "forward"
    PBX = "pbx"
    BYO_REGISTER = "byo_register"


class TrunkStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    ERROR = "error"
    DISABLED = "disabled"


class Transport(StrEnum):
    UDP = "udp"
    TCP = "tcp"
    TLS = "tls"


class Codec(StrEnum):
    PCMA = "PCMA"  # G.711 A-law (UK default)
    PCMU = "PCMU"  # G.711 mu-law
    OPUS = "opus"


class DtmfMode(StrEnum):
    RFC2833 = "rfc2833"
    INBAND = "inband"
    SIP_INFO = "info"


class RouteWhen(StrEnum):
    ALWAYS = "always"
    OUT_OF_HOURS = "out_of_hours"  # AI answers only when the assistant's staff hours are closed
    NO_ANSWER = "no_answer"  # PBX rings staff first and forwards on no-answer (pbx/forward only)


class RegistrationState(StrEnum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    REGISTERED = "registered"
    FAILED = "failed"


class RegistrationStatus(BaseModel):
    state: RegistrationState = RegistrationState.NOT_REQUIRED
    detail: str | None = None
    last_seen_at: datetime | None = None
    expires_at: datetime | None = None


class DdiRoute(BaseModel):
    """One inbound number (DDI) on the trunk and where its calls go."""

    e164: str
    assistant_id: str
    department: str | None = None
    when: RouteWhen = RouteWhen.ALWAYS
    label: str | None = None

    @field_validator("e164")
    @classmethod
    def _e164(cls, v: str) -> str:
        v = re.sub(r"[\s()-]", "", v)
        if not E164.match(v):
            raise ValueError("number must be E.164, e.g. +442012345678")
        return v


class SipTrunk(BaseModel):
    id: str = Field(default_factory=lambda: f"trk-{uuid4().hex[:8]}")
    tenant_id: str
    company_id: str
    name: str = Field(default="Main trunk", min_length=1, max_length=80)
    mode: TrunkMode = TrunkMode.FORWARD
    status: TrunkStatus = TrunkStatus.PENDING
    enabled: bool = True
    provider_preset: str | None = Field(default=None, description="voipfone | sipgate | 3cx | ...")

    # pbx mode: Parlio-issued credentials the customer's PBX authenticates with
    sip_domain: str | None = None
    sip_username: str | None = None
    allowed_ips: list[str] = Field(default_factory=list)
    pbx_address: str | None = Field(default=None, description="host[:port] for extension transfers")

    # byo_register mode: the customer's provider account
    registrar: str | None = None
    username: str | None = None
    auth_username: str | None = None
    outbound_proxy: str | None = None
    register_expires_s: int = Field(default=300, ge=60, le=3600)

    password_sealed: str | None = Field(default=None, exclude=True)

    transport: Transport = Transport.UDP
    codecs: list[Codec] = Field(default_factory=lambda: [Codec.PCMA, Codec.OPUS])
    dtmf: DtmfMode = DtmfMode.RFC2833
    srtp: bool = False

    ddis: list[DdiRoute] = Field(default_factory=list)
    max_concurrent_calls: int = Field(default=2, ge=1, le=500)

    lk_inbound_trunk_id: str | None = None
    lk_outbound_trunk_id: str | None = None
    registration: RegistrationStatus = Field(default_factory=RegistrationStatus)
    last_error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("allowed_ips")
    @classmethod
    def _ips(cls, v: list[str]) -> list[str]:
        for ip in v:
            ipaddress.ip_network(ip, strict=False)
        return v

    @field_validator("registrar", "pbx_address", "outbound_proxy", "sip_domain")
    @classmethod
    def _host(cls, v: str | None) -> str | None:
        if v is not None and not HOST.match(v):
            raise ValueError("expected host[:port]")
        return v

    @model_validator(mode="after")
    def _mode_rules(self) -> SipTrunk:
        if self.srtp and self.transport != Transport.TLS:
            raise ValueError("SRTP requires TLS transport")
        if self.mode == TrunkMode.BYO_REGISTER:
            if not self.registrar or not self.username:
                raise ValueError("byo_register needs registrar and username")
            if any(d.when == RouteWhen.NO_ANSWER for d in self.ddis):
                raise ValueError(
                    "no_answer routing is a PBX feature; use out_of_hours or always for "
                    "registered trunks"
                )
        seen: set[str] = set()
        for d in self.ddis:
            if d.e164 in seen:
                raise ValueError(f"duplicate DDI {d.e164}")
            seen.add(d.e164)
        return self

    @property
    def has_password(self) -> bool:
        return bool(self.password_sealed)

    @property
    def needs_registration(self) -> bool:
        return self.mode == TrunkMode.BYO_REGISTER

    def public(self) -> dict[str, Any]:
        return {**self.model_dump(mode="json"), "has_password": self.has_password}

    def to_doc(self) -> TenantDoc:
        data = self.model_dump(mode="json")
        data["password_sealed"] = self.password_sealed
        return TenantDoc(
            kind=KIND, id=self.id, tenant_id=self.tenant_id, data=data, created_at=self.created_at
        )

    @classmethod
    def from_doc(cls, d: TenantDoc) -> SipTrunk:
        return cls.model_validate(d.data)


class TrunkInput(BaseModel):
    """Dashboard payload: `password` is write-only and sealed on save."""

    name: str = "Main trunk"
    mode: TrunkMode = TrunkMode.FORWARD
    enabled: bool = True
    provider_preset: str | None = None
    allowed_ips: list[str] = Field(default_factory=list)
    pbx_address: str | None = None
    registrar: str | None = None
    username: str | None = None
    auth_username: str | None = None
    outbound_proxy: str | None = None
    register_expires_s: int = 300
    password: str | None = Field(default=None, min_length=4, max_length=200)
    transport: Transport = Transport.UDP
    codecs: list[Codec] = Field(default_factory=lambda: [Codec.PCMA, Codec.OPUS])
    dtmf: DtmfMode = DtmfMode.RFC2833
    srtp: bool = False
    ddis: list[DdiRoute] = Field(default_factory=list)
    max_concurrent_calls: int = 2


class IssuedCredentials(BaseModel):
    """Returned once when Parlio issues PBX credentials; the password is not stored in clear."""

    sip_domain: str
    username: str
    password: str
    transport: Transport
    codecs: list[Codec]
    dtmf: DtmfMode


class TestCallResult(BaseModel):
    ok: bool
    outcome: str
    detail: str | None = None
    simulated: bool = False
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AdmitResult(BaseModel):
    allowed: bool
    trunk_id: str | None = None
    assistant_id: str | None = None
    department: str | None = None
    reason: str | None = None
    active_calls: int = 0
    max_concurrent_calls: int | None = None


# -- routing ---------------------------------------------------------------------------------


def route_decision(
    trunk: SipTrunk, ddi: DdiRoute, staff_hours: Schedule | None, now: datetime | None = None
) -> tuple[bool, str | None]:
    """Should the AI take this call right now? (allowed, reason)."""
    if not trunk.enabled or trunk.status == TrunkStatus.DISABLED:
        return False, "trunk disabled"
    if ddi.when == RouteWhen.OUT_OF_HOURS and staff_hours is not None and staff_hours.is_open(now):
        return False, "staff hours: call left for the PBX/provider"
    return True, None


class ConcurrencyGuard:
    """Per-trunk active call counter (process-local; Redis-backed at multi-API scale)."""

    def __init__(self) -> None:
        self._by_call: dict[str, str] = {}
        self._counts: dict[str, int] = {}

    def active(self, trunk_id: str) -> int:
        return self._counts.get(trunk_id, 0)

    def acquire(self, trunk_id: str, call_id: str, limit: int) -> bool:
        if call_id in self._by_call:
            return True
        if self.active(trunk_id) >= limit:
            return False
        self._by_call[call_id] = trunk_id
        self._counts[trunk_id] = self.active(trunk_id) + 1
        return True

    def release(self, call_id: str) -> None:
        trunk_id = self._by_call.pop(call_id, None)
        if trunk_id is not None:
            self._counts[trunk_id] = max(0, self.active(trunk_id) - 1)


# -- provisioning seam -----------------------------------------------------------------------


class SipProvisioner(Protocol):
    async def ensure_inbound(self, trunk: SipTrunk, password: str | None) -> str: ...
    async def ensure_outbound(self, trunk: SipTrunk, password: str | None) -> str | None: ...
    async def remove(self, trunk: SipTrunk) -> None: ...
    async def test_call(self, trunk: SipTrunk, to: str) -> TestCallResult: ...


class SimulatedProvisioner:
    def __init__(self) -> None:
        self.inbound: dict[str, dict[str, Any]] = {}
        self.outbound: dict[str, dict[str, Any]] = {}
        self.removed: list[str] = []
        self.test_outcome = "answered"

    async def ensure_inbound(self, trunk: SipTrunk, password: str | None) -> str:
        tid = trunk.lk_inbound_trunk_id or f"ST_in_{trunk.id}"
        self.inbound[tid] = {
            "numbers": [d.e164 for d in trunk.ddis],
            "auth_username": trunk.sip_username,
            "has_password": bool(password),
            "allowed_addresses": list(trunk.allowed_ips),
        }
        return tid

    async def ensure_outbound(self, trunk: SipTrunk, password: str | None) -> str | None:
        address = trunk.pbx_address if trunk.mode == TrunkMode.PBX else trunk.registrar
        if not address:
            return None
        tid = trunk.lk_outbound_trunk_id or f"ST_out_{trunk.id}"
        self.outbound[tid] = {"address": address, "transport": trunk.transport}
        return tid

    async def remove(self, trunk: SipTrunk) -> None:
        self.removed.append(trunk.id)
        self.inbound.pop(trunk.lk_inbound_trunk_id or "", None)
        self.outbound.pop(trunk.lk_outbound_trunk_id or "", None)

    async def test_call(self, trunk: SipTrunk, to: str) -> TestCallResult:
        ok = self.test_outcome == "answered"
        return TestCallResult(
            ok=ok,
            outcome=self.test_outcome,
            detail=f"simulated {trunk.mode} test to {to}",
            simulated=True,
        )


class Registrar(Protocol):
    async def status(self, trunk: SipTrunk) -> RegistrationStatus: ...


class SimulatedRegistrar:
    def __init__(self, state: RegistrationState = RegistrationState.PENDING) -> None:
        self.state = state

    async def status(self, trunk: SipTrunk) -> RegistrationStatus:
        if not trunk.needs_registration:
            return RegistrationStatus(state=RegistrationState.NOT_REQUIRED)
        detail = {
            RegistrationState.PENDING: "registration agent not deployed in this environment",
            RegistrationState.REGISTERED: f"registered to {trunk.registrar}",
            RegistrationState.FAILED: "401 Unauthorized from registrar",
        }.get(self.state)
        return RegistrationStatus(
            state=self.state,
            detail=detail,
            last_seen_at=datetime.now(UTC) if self.state == RegistrationState.REGISTERED else None,
        )


# -- service ---------------------------------------------------------------------------------


class SipService:
    def __init__(
        self,
        store: CallStore,
        vault: Vault,
        provisioner: SipProvisioner,
        registrar: Registrar,
        *,
        sip_domain: str,
    ) -> None:
        self.store = store
        self.vault = vault
        self.provisioner = provisioner
        self.registrar = registrar
        self.sip_domain = sip_domain
        self.guard = ConcurrencyGuard()

    async def trunks(self, tenant_id: str) -> list[SipTrunk]:
        docs = await self.store.list_docs(KIND, tenant_id)
        return sorted((SipTrunk.from_doc(d) for d in docs), key=lambda t: t.created_at)

    async def get(self, tenant_id: str, trunk_id: str) -> SipTrunk | None:
        d = await self.store.get_doc(KIND, trunk_id)
        if d is None or d.tenant_id != tenant_id:
            return None
        return SipTrunk.from_doc(d)

    async def create(
        self, tenant_id: str, company_id: str, body: TrunkInput
    ) -> tuple[SipTrunk, IssuedCredentials | None]:
        trunk = SipTrunk(tenant_id=tenant_id, company_id=company_id, **_fields(body))
        return await self._save(trunk, body.password, issue=trunk.mode == TrunkMode.PBX)

    async def update(
        self, tenant_id: str, trunk_id: str, body: TrunkInput
    ) -> tuple[SipTrunk, IssuedCredentials | None] | None:
        prev = await self.get(tenant_id, trunk_id)
        if prev is None:
            return None
        trunk = SipTrunk.model_validate(
            {**prev.model_dump(), **_fields(body), "password_sealed": prev.password_sealed}
        )
        if trunk.mode != prev.mode:
            trunk.password_sealed = None
            trunk.sip_username = None
        for d in prev.ddis:
            if d.e164 not in {n.e164 for n in trunk.ddis}:
                await self.store.unassign_number(d.e164)
        issue = trunk.mode == TrunkMode.PBX and not trunk.sip_username
        return await self._save(trunk, body.password, issue=issue)

    async def _save(
        self, trunk: SipTrunk, password: str | None, *, issue: bool
    ) -> tuple[SipTrunk, IssuedCredentials | None]:
        issued: IssuedCredentials | None = None
        if trunk.mode == TrunkMode.PBX:
            trunk.sip_domain = self.sip_domain
            if issue or not trunk.sip_username:
                trunk.sip_username = f"t{trunk.tenant_id[:6]}-{secrets.token_hex(3)}"
                password = secrets.token_urlsafe(18)
                issued = IssuedCredentials(
                    sip_domain=self.sip_domain,
                    username=trunk.sip_username,
                    password=password,
                    transport=trunk.transport,
                    codecs=trunk.codecs,
                    dtmf=trunk.dtmf,
                )
        if password:
            trunk.password_sealed = self.vault.seal(password)
        if trunk.mode == TrunkMode.BYO_REGISTER and not trunk.password_sealed:
            trunk.status = TrunkStatus.PENDING
            trunk.last_error = "provider password required"
        else:
            await self._provision(trunk, password)
        trunk.registration = await self.registrar.status(trunk)
        for d in trunk.ddis:
            await self.store.assign_number(
                trunk.tenant_id, trunk.company_id, d.e164, d.assistant_id
            )
        await self.store.put_doc(trunk.to_doc())
        return trunk, issued

    async def _provision(self, trunk: SipTrunk, password: str | None) -> None:
        try:
            if trunk.mode == TrunkMode.FORWARD:
                trunk.status = TrunkStatus.ACTIVE
                return
            pw = password
            if pw is None and trunk.password_sealed:
                pw = self.vault.open(trunk.password_sealed)
            trunk.lk_inbound_trunk_id = await self.provisioner.ensure_inbound(trunk, pw)
            trunk.lk_outbound_trunk_id = await self.provisioner.ensure_outbound(trunk, pw)
            trunk.status = TrunkStatus.ACTIVE if trunk.enabled else TrunkStatus.DISABLED
            trunk.last_error = None
        except Exception as e:
            log.warning("provisioning trunk %s failed: %s", trunk.id, e)
            trunk.status = TrunkStatus.ERROR
            trunk.last_error = str(e)[:300]

    async def rotate_credentials(self, tenant_id: str, trunk_id: str) -> IssuedCredentials | None:
        trunk = await self.get(tenant_id, trunk_id)
        if trunk is None or trunk.mode != TrunkMode.PBX:
            return None
        _, issued = await self._save(trunk, None, issue=True)
        return issued

    async def delete(self, tenant_id: str, trunk_id: str) -> bool:
        trunk = await self.get(tenant_id, trunk_id)
        if trunk is None:
            return False
        try:
            await self.provisioner.remove(trunk)
        except Exception:
            log.warning("deprovision of %s failed", trunk_id, exc_info=True)
        for d in trunk.ddis:
            await self.store.unassign_number(d.e164)
        return await self.store.delete_doc(KIND, trunk_id)

    async def refresh_status(self, tenant_id: str, trunk_id: str) -> SipTrunk | None:
        trunk = await self.get(tenant_id, trunk_id)
        if trunk is None:
            return None
        trunk.registration = await self.registrar.status(trunk)
        await self.store.put_doc(trunk.to_doc())
        return trunk

    async def test_call(
        self, tenant_id: str, trunk_id: str, to: str | None
    ) -> TestCallResult | None:
        trunk = await self.get(tenant_id, trunk_id)
        if trunk is None:
            return None
        target = to or (trunk.ddis[0].e164 if trunk.ddis else None)
        if not target:
            return TestCallResult(ok=False, outcome="no_target", detail="add a DDI or pass `to`")
        if trunk.status != TrunkStatus.ACTIVE:
            return TestCallResult(ok=False, outcome="trunk_not_active", detail=trunk.last_error)
        try:
            return await self.provisioner.test_call(trunk, target)
        except Exception as e:
            return TestCallResult(ok=False, outcome="error", detail=str(e)[:300])

    # inbound admission (worker)
    async def find_route(self, e164: str) -> tuple[SipTrunk, DdiRoute] | None:
        for d in await self.store.list_docs(KIND, None, 10_000):
            trunk = SipTrunk.from_doc(d)
            for ddi in trunk.ddis:
                if ddi.e164 == e164:
                    return trunk, ddi
        return None

    async def admit(self, e164: str, call_id: str, now: datetime | None = None) -> AdmitResult:
        found = await self.find_route(e164)
        if found is None:
            return AdmitResult(allowed=True, reason="not a trunk DDI")
        trunk, ddi = found
        cfg = await self.store.get_assistant(ddi.assistant_id)
        allowed, reason = route_decision(trunk, ddi, cfg.hours if cfg else None, now)
        if not allowed:
            return AdmitResult(
                allowed=False, trunk_id=trunk.id, assistant_id=ddi.assistant_id, reason=reason
            )
        if not self.guard.acquire(trunk.id, call_id, trunk.max_concurrent_calls):
            return AdmitResult(
                allowed=False,
                trunk_id=trunk.id,
                assistant_id=ddi.assistant_id,
                reason="concurrent call limit reached",
                active_calls=self.guard.active(trunk.id),
                max_concurrent_calls=trunk.max_concurrent_calls,
            )
        return AdmitResult(
            allowed=True,
            trunk_id=trunk.id,
            assistant_id=ddi.assistant_id,
            department=ddi.department,
            active_calls=self.guard.active(trunk.id),
            max_concurrent_calls=trunk.max_concurrent_calls,
        )

    def release(self, call_id: str) -> None:
        self.guard.release(call_id)


def _fields(body: TrunkInput) -> dict[str, Any]:
    return body.model_dump(exclude={"password"})


# -- provider setup guides -------------------------------------------------------------------


class ProviderGuide(BaseModel):
    id: str
    name: str
    mode: TrunkMode
    summary: str
    steps: list[str]
    quirks: list[str] = Field(default_factory=list)
    defaults: dict[str, Any] = Field(default_factory=dict)


PROVIDER_GUIDES: list[ProviderGuide] = [
    ProviderGuide(
        id="forward",
        name="Call forwarding (any provider)",
        mode=TrunkMode.FORWARD,
        summary="Simplest: forward your existing number to your ParlioTec number. No SIP needed.",
        steps=[
            "In your phone provider's portal enable call forwarding (always, or on no-answer /"
            " busy for overflow) to your ParlioTec number.",
            "Add your forwarded number as a DDI here so the assistant greets callers correctly.",
            "Call your number to test; the assistant should answer within a ring.",
        ],
        quirks=["Some providers charge forwarding minutes; check your tariff."],
    ),
    ProviderGuide(
        id="mobile",
        name="Mobile phone (EE, O2, Vodafone, Three, giffgaff...)",
        mode=TrunkMode.FORWARD,
        summary=(
            "No landline needed: divert your business mobile to your ParlioTec number, either"
            " always or only when you can't answer."
        ),
        steps=[
            "Decide how much ParlioTec should handle. Overflow only: dial **61*<ParlioTec number>**"
            " to divert unanswered calls (add *11 before the final # to set the ring time to"
            " ~15s, e.g. **61*<number>*11*15#), **67*<number># when busy and"
            " **62*<number># when out of signal. Everything: dial **21*<number>#.",
            "Enter your ParlioTec number in international format (+44... or 0044...) exactly as"
            " shown on this page.",
            "Add your mobile number as a DDI here so the assistant knows which business the"
            " call is for and can text callers back from the right identity.",
            "Test by calling your mobile from another phone and letting it ring out; ParlioTec"
            " should answer within a ring of the divert.",
            "To switch off later: ##61#, ##67#, ##62# or ##21# (or ##002# to clear all).",
        ],
        quirks=[
            "Most UK unlimited tariffs include diverted calls, but some PAYG/business SIMs"
            " charge for the diverted leg - check before going live.",
            "Diverts are set per SIM; on dual-SIM phones set them on the business line.",
            "Caller ID is passed through, so contacts, SMS follow-ups and returning-caller"
            " detection work as normal.",
            "iPhone/Android call-forwarding settings only offer 'always'; use the ** codes"
            " (or your network app) for no-answer/busy diverts.",
        ],
    ),
    ProviderGuide(
        id="voipfone",
        name="Voipfone",
        mode=TrunkMode.BYO_REGISTER,
        summary="ParlioTec registers as an extension on your Voipfone account.",
        steps=[
            "Voipfone control panel: Services > Extensions > Add extension; note the extension"
            " number and password.",
            "Enter registrar sip.voipfone.net, username = extension number, the password, and"
            " your Voipfone number(s) as DDIs.",
            "Route the number to that extension in Voipfone (Numbers > Route to extension).",
        ],
        quirks=[
            "One registration per extension: do not use the same extension on a desk phone.",
            "Voipfone sends DTMF as RFC 2833 and prefers G.711 A-law.",
        ],
        defaults={"registrar": "sip.voipfone.net", "transport": "udp", "codecs": ["PCMA"]},
    ),
    ProviderGuide(
        id="sipgate",
        name="Sipgate",
        mode=TrunkMode.BYO_REGISTER,
        summary="ParlioTec registers with a sipgate SIP credential (device).",
        steps=[
            "sipgate: Phone settings > add a VoIP phone/device; copy its SIP ID and password.",
            "Registrar sipgate.co.uk (UK) or sipgate.de (DE); username = SIP ID.",
            "Assign your number to that device in sipgate routing.",
        ],
        quirks=["Sub-accounts have separate SIP IDs; use the one routed to your number."],
        defaults={"registrar": "sipgate.co.uk", "transport": "udp"},
    ),
    ProviderGuide(
        id="gamma",
        name="Gamma SIP Trunk",
        mode=TrunkMode.PBX,
        summary="Point a Gamma trunk (or your Gamma-connected PBX) at ParlioTec's SIP edge.",
        steps=[
            "Create a PBX trunk here; ParlioTec issues a SIP username/password and domain.",
            "Ask Gamma (or configure your PBX) to deliver your DDIs to that address with digest"
            " auth, or add Gamma's media IPs to the allowlist for IP auth.",
            "Add each DDI and the assistant it should reach.",
        ],
        quirks=["Gamma trunks are IP-authenticated by default; allowlist their signalling IPs."],
        defaults={"transport": "udp", "codecs": ["PCMA"], "dtmf": "rfc2833"},
    ),
    ProviderGuide(
        id="3cx",
        name="3CX",
        mode=TrunkMode.PBX,
        summary="Add ParlioTec as a generic SIP trunk in 3CX and route out-of-hours/no-answer "
        "to it.",
        steps=[
            "Create a PBX trunk here and copy the issued credentials.",
            "3CX admin: Voice & Chat > Add SIP trunk > Generic (registration based); paste the"
            " registrar/domain, username and password.",
            "Inbound rules: send the DDI to a Ring Group, with 'no answer'/out-of-office"
            " destination = External number via the ParlioTec trunk (or route directly to it).",
            "For AI-to-staff transfers, enter your 3CX public address as the PBX address.",
        ],
        quirks=[
            "3CX blocks unknown trunks by default: add ParlioTec's edge IP to its allowlist.",
            "Use 'Register' mode; 3CX needs an outbound caller ID on the trunk.",
        ],
        defaults={"transport": "udp", "codecs": ["PCMA", "opus"], "dtmf": "rfc2833"},
    ),
    ProviderGuide(
        id="freepbx",
        name="FreePBX / Asterisk",
        mode=TrunkMode.PBX,
        summary="pjsip trunk from FreePBX to ParlioTec; extensions reachable for transfers.",
        steps=[
            "Create a PBX trunk here and copy the issued credentials.",
            "FreePBX: Connectivity > Trunks > Add pjsip trunk; SIP server = ParlioTec domain,"
            " username/secret as issued, registration Send.",
            "Inbound route or time condition: after-hours destination = trunk (custom"
            " destination dialling the DDI).",
            "Set the PBX address so ParlioTec can dial extensions back.",
        ],
        quirks=["Set DTMF mode rfc4733 (RFC 2833) on the trunk; disable inband."],
        defaults={"transport": "udp", "codecs": ["PCMA", "opus"], "dtmf": "rfc2833"},
    ),
    ProviderGuide(
        id="ringcentral",
        name="RingCentral",
        mode=TrunkMode.FORWARD,
        summary="RingCentral does not offer customer SIP trunks; use forwarding or overflow rules.",
        steps=[
            "Admin portal: Phone System > Numbers > forward the number (or an After Hours rule)"
            " to your ParlioTec number.",
            "Add the number as a DDI here.",
        ],
        quirks=["Call queues can overflow to an external number after N seconds."],
    ),
    ProviderGuide(
        id="other_pbx",
        name="Other PBX (generic SIP)",
        mode=TrunkMode.PBX,
        summary="Any PBX that can register or send calls to a SIP URI with digest auth.",
        steps=[
            "Create a PBX trunk here; note domain, username, password.",
            "Configure a SIP trunk on your PBX with those details (UDP 5060 or TLS 5061).",
            "Route DDIs / time conditions to the trunk; add them here with their assistant.",
        ],
        quirks=["If your PBX uses IP auth only, add its public IP(s) to the allowlist."],
    ),
]
