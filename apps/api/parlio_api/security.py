"""Phase 14 account security: TOTP 2FA with recovery codes, per-tenant enforcement, session /
device list with revoke, and enterprise SSO / SCIM configuration seams.

Identity (passwords, Google login) stays with Supabase; this module adds a second factor that is
verified by the API itself so it works identically in dev and Supabase modes and can be enforced
per organisation (`SecurityPolicy.require_2fa`: off | admins | all). After a successful TOTP or
recovery-code check the API issues a short-lived signed MFA token (`X-Parlio-MFA` header) that is
also the session record shown in "Sessions & devices".

TOTP secrets and recovery codes are stored under a per-user pseudo-tenant (`user:<id>`) so they
are never mixed into organisation data; the secret is sealed with the vault, recovery codes are
stored as salted hashes only.

SSO (SAML / OIDC via Supabase enterprise providers) and SCIM are delivered as *configuration and
provisioning seams*: the policy records the IdP details and enforced-domain, and the SCIM v2
`/Users` endpoint (bearer token, hashed at rest) creates / deactivates members. Neither claims a
live IdP connection until the Supabase SSO provider is registered for the tenant.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import struct
import time
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from urllib.parse import quote
from uuid import uuid4

from pydantic import BaseModel, Field

from parlio_api.store import CallStore, Member, TenantDoc
from parlio_api.vault import Vault

log = logging.getLogger("parlio.api.security")

POLICY_KIND = "security_policy"
TOTP_KIND = "totp"
SESSION_KIND = "session"
SCIM_KIND = "scim_token"
RECOVERY_CODES = 8
MFA_TTL_HOURS = 12


def user_scope(user_id: str) -> str:
    return f"user:{user_id}"


# -- TOTP (RFC 6238) -------------------------------------------------------------------------------


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp_code(secret_b32: str, at: float | None = None, step: int = 30, digits: int = 6) -> str:
    key = base64.b32decode(secret_b32 + "=" * (-len(secret_b32) % 8))
    counter = int((at if at is not None else time.time()) // step)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % (10**digits)
    return f"{code:0{digits}d}"


def verify_totp(secret_b32: str, code: str, at: float | None = None, window: int = 1) -> bool:
    code = code.strip().replace(" ", "")
    if not code.isdigit():
        return False
    now = at if at is not None else time.time()
    return any(
        hmac.compare_digest(totp_code(secret_b32, now + 30 * i), code)
        for i in range(-window, window + 1)
    )


def otpauth_uri(secret_b32: str, email: str, issuer: str = "ParlioTec") -> str:
    return (
        f"otpauth://totp/{quote(issuer)}:{quote(email)}?secret={secret_b32}"
        f"&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period=30"
    )


def _hash_code(code: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{code.strip().lower()}".encode()).hexdigest()


# -- models ----------------------------------------------------------------------------------------


class Require2FA(StrEnum):
    OFF = "off"
    ADMINS = "admins"
    ALL = "all"


class SsoProvider(StrEnum):
    NONE = "none"
    ENTRA = "microsoft_entra"
    GOOGLE = "google_workspace"
    OKTA = "okta"
    SAML = "saml"
    OIDC = "oidc"


class SsoConfig(BaseModel):
    provider: SsoProvider = SsoProvider.NONE
    protocol: str = Field("oidc", pattern=r"^(oidc|saml)$")
    domains: list[str] = Field(default_factory=list, description="email domains routed to SSO")
    issuer: str | None = None
    client_id: str | None = None
    metadata_url: str | None = Field(None, description="SAML metadata / OIDC discovery URL")
    enforce: bool = Field(False, description="members on these domains must sign in via SSO")
    supabase_provider_id: str | None = Field(
        None,
        description="Set once the provider is registered with Supabase SSO; until then the "
        "configuration is stored but not live.",
    )

    @property
    def status(self) -> str:
        if self.provider == SsoProvider.NONE:
            return "off"
        return "live" if self.supabase_provider_id else "configured_not_live"


class ScimConfig(BaseModel):
    enabled: bool = False
    default_role: str = Field("member", pattern=r"^(admin|member|viewer)$")
    token_hint: str | None = None  # last 4 chars of the issued token
    issued_at: datetime | None = None


class SecurityPolicy(BaseModel):
    tenant_id: str
    require_2fa: Require2FA = Require2FA.OFF
    session_hours: int = Field(MFA_TTL_HOURS, ge=1, le=24 * 30)
    sso: SsoConfig = Field(default_factory=SsoConfig)
    scim: ScimConfig = Field(default_factory=ScimConfig)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def requires_2fa_for(self, role: str | None) -> bool:
        if self.require_2fa == Require2FA.ALL:
            return True
        return self.require_2fa == Require2FA.ADMINS and role in ("owner", "admin")


class TotpEnrolment(BaseModel):
    user_id: str
    email: str
    sealed_secret: str
    confirmed: bool = False
    salt: str = Field(default_factory=lambda: secrets.token_hex(8))
    recovery_hashes: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    confirmed_at: datetime | None = None
    last_used_at: datetime | None = None


class TwoFactorStatus(BaseModel):
    enrolled: bool
    confirmed: bool
    recovery_codes_left: int = 0
    mfa_verified: bool = False


class EnrolmentStart(BaseModel):
    secret: str
    otpauth_uri: str


class RecoveryCodes(BaseModel):
    codes: list[str]


class Session(BaseModel):
    id: str = Field(default_factory=lambda: f"ss-{uuid4().hex[:10]}")
    user_id: str
    label: str  # browser / device summary from the User-Agent
    ip: str | None = None
    mfa: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_seen_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime
    revoked_at: datetime | None = None
    current: bool = False

    @property
    def active(self) -> bool:
        return self.revoked_at is None and self.expires_at > datetime.now(UTC)


class MfaToken(BaseModel):
    token: str
    session_id: str
    expires_at: datetime


def device_label(user_agent: str | None) -> str:
    ua = (user_agent or "").lower()
    browser = next(
        (
            n
            for k, n in (
                ("edg/", "Edge"),
                ("chrome/", "Chrome"),
                ("safari/", "Safari"),
                ("firefox/", "Firefox"),
            )
            if k in ua
        ),
        "Browser",
    )
    os_ = next(
        (
            n
            for k, n in (
                ("windows", "Windows"),
                ("iphone", "iPhone"),
                ("ipad", "iPad"),
                ("mac os", "macOS"),
                ("android", "Android"),
                ("linux", "Linux"),
            )
            if k in ua
        ),
        "unknown device",
    )
    return f"{browser} on {os_}"


# -- service ---------------------------------------------------------------------------------------


class SecurityService:
    def __init__(self, store: CallStore, vault: Vault, signing_key: str) -> None:
        self.store = store
        self.vault = vault
        self._key = signing_key.encode()

    # policy
    async def policy(self, tenant_id: str) -> SecurityPolicy:
        doc = await self.store.get_doc(POLICY_KIND, tenant_id)
        return (
            SecurityPolicy.model_validate(doc.data) if doc else SecurityPolicy(tenant_id=tenant_id)
        )

    async def save_policy(self, p: SecurityPolicy) -> SecurityPolicy:
        p.updated_at = datetime.now(UTC)
        await self.store.put_doc(
            TenantDoc(
                kind=POLICY_KIND,
                id=p.tenant_id,
                tenant_id=p.tenant_id,
                data=p.model_dump(mode="json"),
            )
        )
        return p

    # enrolment
    async def enrolment(self, user_id: str) -> TotpEnrolment | None:
        doc = await self.store.get_doc(TOTP_KIND, user_id)
        return TotpEnrolment.model_validate(doc.data) if doc else None

    async def _save_enrolment(self, e: TotpEnrolment) -> None:
        await self.store.put_doc(
            TenantDoc(
                kind=TOTP_KIND,
                id=e.user_id,
                tenant_id=user_scope(e.user_id),
                data=e.model_dump(mode="json"),
                created_at=e.created_at,
            )
        )

    async def status(self, user_id: str, mfa_verified: bool) -> TwoFactorStatus:
        e = await self.enrolment(user_id)
        return TwoFactorStatus(
            enrolled=e is not None,
            confirmed=bool(e and e.confirmed),
            recovery_codes_left=len(e.recovery_hashes) if e else 0,
            mfa_verified=mfa_verified,
        )

    async def start_enrolment(self, user_id: str, email: str) -> EnrolmentStart:
        existing = await self.enrolment(user_id)
        if existing is not None and existing.confirmed:
            raise ValueError("2FA is already enabled; disable it first to re-enrol")
        secret = new_secret()
        await self._save_enrolment(
            TotpEnrolment(user_id=user_id, email=email, sealed_secret=self.vault.seal(secret))
        )
        return EnrolmentStart(secret=secret, otpauth_uri=otpauth_uri(secret, email))

    def _new_recovery(self, e: TotpEnrolment) -> list[str]:
        codes = [
            f"{secrets.token_hex(2)}-{secrets.token_hex(2)}-{secrets.token_hex(2)}"
            for _ in range(RECOVERY_CODES)
        ]
        e.recovery_hashes = [_hash_code(c, e.salt) for c in codes]
        return codes

    async def confirm_enrolment(self, user_id: str, code: str) -> RecoveryCodes:
        e = await self.enrolment(user_id)
        if e is None:
            raise ValueError("start enrolment first")
        if e.confirmed:
            raise ValueError("already confirmed")
        if not verify_totp(self.vault.open(e.sealed_secret), code):
            raise ValueError("code did not match — check the time on your device")
        e.confirmed = True
        e.confirmed_at = datetime.now(UTC)
        codes = self._new_recovery(e)
        await self._save_enrolment(e)
        return RecoveryCodes(codes=codes)

    async def regenerate_recovery(self, user_id: str, code: str) -> RecoveryCodes:
        e = await self.enrolment(user_id)
        if e is None or not e.confirmed:
            raise ValueError("2FA is not enabled")
        if not verify_totp(self.vault.open(e.sealed_secret), code):
            raise ValueError("code did not match")
        codes = self._new_recovery(e)
        await self._save_enrolment(e)
        return RecoveryCodes(codes=codes)

    async def reset_by_staff(self, user_id: str) -> bool:
        """Staff-initiated 2FA reset (lost device): drop enrolment, revoke MFA sessions."""
        had = await self.store.delete_doc(TOTP_KIND, user_id)
        for s in await self.sessions(user_id):
            if s.mfa:
                await self.revoke(user_id, s.id)
        return had

    async def disable(self, user_id: str, code: str) -> bool:
        e = await self.enrolment(user_id)
        if e is None:
            return False
        if e.confirmed and not (
            verify_totp(self.vault.open(e.sealed_secret), code) or self._use_recovery(e, code)
        ):
            raise ValueError("code did not match")
        await self.store.delete_doc(TOTP_KIND, user_id)
        for s in await self.sessions(user_id):
            if s.mfa:
                await self.revoke(user_id, s.id)
        return True

    def _use_recovery(self, e: TotpEnrolment, code: str) -> bool:
        h = _hash_code(code, e.salt)
        if h not in e.recovery_hashes:
            return False
        e.recovery_hashes.remove(h)
        return True

    async def verify(
        self,
        user_id: str,
        code: str,
        *,
        user_agent: str | None,
        ip: str | None,
        session_hours: int = MFA_TTL_HOURS,
    ) -> MfaToken:
        e = await self.enrolment(user_id)
        if e is None or not e.confirmed:
            raise ValueError("2FA is not enabled for this account")
        ok = verify_totp(self.vault.open(e.sealed_secret), code)
        if not ok:
            ok = self._use_recovery(e, code)
        if not ok:
            raise ValueError("code did not match")
        e.last_used_at = datetime.now(UTC)
        await self._save_enrolment(e)
        session = await self._create_session(user_id, user_agent, ip, mfa=True, hours=session_hours)
        return MfaToken(
            token=self._sign(user_id, session.id, session.expires_at),
            session_id=session.id,
            expires_at=session.expires_at,
        )

    # sessions
    async def _create_session(
        self, user_id: str, user_agent: str | None, ip: str | None, *, mfa: bool, hours: int
    ) -> Session:
        s = Session(
            user_id=user_id,
            label=device_label(user_agent),
            ip=ip,
            mfa=mfa,
            expires_at=datetime.now(UTC) + timedelta(hours=hours),
        )
        await self._save_session(s)
        return s

    async def _save_session(self, s: Session) -> None:
        await self.store.put_doc(
            TenantDoc(
                kind=SESSION_KIND,
                id=s.id,
                tenant_id=user_scope(s.user_id),
                data=s.model_dump(mode="json", exclude={"current"}),
                created_at=s.created_at,
            )
        )

    async def sessions(self, user_id: str, current_id: str | None = None) -> list[Session]:
        docs = await self.store.list_docs(SESSION_KIND, user_scope(user_id), 100)
        out = [Session.model_validate(d.data) for d in docs]
        out = [s for s in out if s.active]
        for s in out:
            s.current = s.id == current_id
        out.sort(key=lambda s: s.last_seen_at, reverse=True)
        return out

    async def touch(self, user_id: str, user_agent: str | None, ip: str | None) -> Session:
        """Record a plain (non-MFA) sign-in device so it appears in the device list."""
        label = device_label(user_agent)
        for s in await self.sessions(user_id):
            if not s.mfa and s.label == label and s.ip == ip:
                s.last_seen_at = datetime.now(UTC)
                await self._save_session(s)
                return s
        return await self._create_session(user_id, user_agent, ip, mfa=False, hours=24 * 30)

    async def revoke(self, user_id: str, session_id: str) -> bool:
        doc = await self.store.get_doc(SESSION_KIND, session_id)
        if doc is None or doc.tenant_id != user_scope(user_id):
            return False
        s = Session.model_validate(doc.data)
        s.revoked_at = datetime.now(UTC)
        await self._save_session(s)
        return True

    async def revoke_all(self, user_id: str, except_id: str | None = None) -> int:
        n = 0
        for s in await self.sessions(user_id):
            if s.id != except_id and await self.revoke(user_id, s.id):
                n += 1
        return n

    # mfa token
    def _sign(self, user_id: str, session_id: str, exp: datetime) -> str:
        payload = f"{user_id}|{session_id}|{int(exp.timestamp())}"
        sig = hmac.new(self._key, payload.encode(), hashlib.sha256).hexdigest()[:32]
        return base64.urlsafe_b64encode(f"{payload}|{sig}".encode()).decode().rstrip("=")

    async def check_mfa(self, user_id: str, token: str | None) -> Session | None:
        """Return the live MFA session if the header token is valid for this user."""
        if not token:
            return None
        try:
            raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode()
            uid, sid, exp, sig = raw.split("|")
        except Exception:
            return None
        payload = f"{uid}|{sid}|{exp}"
        good = hmac.new(self._key, payload.encode(), hashlib.sha256).hexdigest()[:32]
        if uid != user_id or not hmac.compare_digest(good, sig):
            return None
        if int(exp) < time.time():
            return None
        doc = await self.store.get_doc(SESSION_KIND, sid)
        if doc is None or doc.tenant_id != user_scope(user_id):
            return None
        s = Session.model_validate(doc.data)
        if not s.active or not s.mfa:
            return None
        if (datetime.now(UTC) - s.last_seen_at) > timedelta(minutes=5):
            s.last_seen_at = datetime.now(UTC)
            await self._save_session(s)
        return s

    async def mfa_required(self, tenant_id: str, role: str | None, user_id: str) -> bool:
        """Does this member need a verified MFA session to use tenant `tenant_id`?"""
        p = await self.policy(tenant_id)
        return p.requires_2fa_for(role)

    # SCIM
    async def issue_scim_token(self, tenant_id: str) -> str:
        token = f"scim_{secrets.token_urlsafe(32)}"
        await self.store.put_doc(
            TenantDoc(
                kind=SCIM_KIND,
                id=tenant_id,
                tenant_id=tenant_id,
                data={"hash": hashlib.sha256(token.encode()).hexdigest()},
            )
        )
        p = await self.policy(tenant_id)
        p.scim.enabled = True
        p.scim.token_hint = token[-4:]
        p.scim.issued_at = datetime.now(UTC)
        await self.save_policy(p)
        return token

    async def revoke_scim_token(self, tenant_id: str) -> None:
        await self.store.delete_doc(SCIM_KIND, tenant_id)
        p = await self.policy(tenant_id)
        p.scim.enabled = False
        p.scim.token_hint = None
        await self.save_policy(p)

    async def tenant_for_scim_token(self, token: str) -> str | None:
        """SCIM tokens embed no tenant; look the hash up across tenants (bounded, hashed)."""
        h = hashlib.sha256(token.encode()).hexdigest()
        for d in await self.store.list_docs(SCIM_KIND, None, 1000):
            if hmac.compare_digest(str(d.data.get("hash", "")), h):
                p = await self.policy(d.tenant_id)
                return d.tenant_id if p.scim.enabled else None
        return None

    async def scim_upsert_user(
        self, tenant_id: str, *, email: str, name: str | None, active: bool, external_id: str | None
    ) -> Member:
        p = await self.policy(tenant_id)
        members = await self.store.list_members(tenant_id)
        existing = next((m for m in members if m.email.lower() == email.lower()), None)
        if not active:
            if existing is not None and existing.role != "owner":
                await self.store.remove_member(tenant_id, existing.user_id)
                return existing.model_copy(update={"status": "removed"})
            return existing or Member(
                tenant_id=tenant_id,
                user_id=external_id or f"scim-{uuid4().hex[:8]}",
                email=email,
                status="removed",
            )
        if existing is not None:
            return await self.store.upsert_member(
                existing.model_copy(update={"name": name or existing.name})
            )
        return await self.store.upsert_member(
            Member(
                tenant_id=tenant_id,
                user_id=external_id or f"scim-{uuid4().hex[:8]}",
                email=email.lower(),
                name=name,
                role=p.scim.default_role,
                status="invited",
            )
        )
