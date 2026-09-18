"""Dashboard authentication.

Two modes, selected by `PARLIO_AUTH_MODE`:

* ``dev`` (default) - no credentials required. The principal is the seeded demo owner, or the
  email given in ``X-Parlio-User`` (handy for multi-user testing). Never enable in production.
* ``supabase`` - ``Authorization: Bearer <access_token>`` issued by Supabase Auth (email/password
  or Google OAuth in the dashboard). Tokens are verified locally: legacy HS256 tokens with
  ``PARLIO_SUPABASE_JWT_SECRET``, and ES256/RS256 tokens (the default for new Supabase projects)
  against the project's JWKS at ``<PARLIO_SUPABASE_URL>/auth/v1/.well-known/jwks.json`` (cached).

Tenant membership always comes from our own store (`memberships`), never from the token, so an
org admin can invite/remove users without touching the identity provider.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from contextlib import suppress
from typing import Annotated, Any

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from fastapi import Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from parlio_api.admin import PLATFORM_TENANT, STAFF_ROLES
from parlio_api.deps import AdminDep, SecurityDep, SettingsDep, StoreDep
from parlio_api.security import SecurityService
from parlio_api.settings import Settings
from parlio_api.store import CallStore, Member

DEV_USER_EMAIL = "owner@demo.parlio.local"
DEV_TENANT = "demo"


class Principal(BaseModel):
    user_id: str
    email: str
    name: str | None = None
    memberships: list[Member] = Field(default_factory=list)
    mode: str = "dev"
    mfa_verified: bool = False
    mfa_session_id: str | None = None
    mfa_required: list[str] = Field(
        default_factory=list, description="tenants whose policy needs a verified MFA session"
    )
    view_as: str | None = Field(
        default=None, description="tenant a platform staff member is viewing read-only"
    )

    @property
    def tenant_ids(self) -> list[str]:
        return [
            m.tenant_id
            for m in self.memberships
            if m.status == "active" and m.tenant_id != PLATFORM_TENANT
        ]

    @property
    def staff_role(self) -> str | None:
        return next(
            (
                m.role
                for m in self.memberships
                if m.tenant_id == PLATFORM_TENANT and m.status == "active" and m.role in STAFF_ROLES
            ),
            None,
        )

    @property
    def tenant_memberships(self) -> list[Member]:
        return [m for m in self.memberships if m.tenant_id != PLATFORM_TENANT]

    def role_in(self, tenant_id: str) -> str | None:
        return next((m.role for m in self.memberships if m.tenant_id == tenant_id), None)

    def require_tenant(self, tenant_id: str) -> None:
        if tenant_id not in self.tenant_ids:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "not a member of this organisation")
        if tenant_id in self.mfa_required and not self.mfa_verified:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "two-factor verification required",
                headers={"X-Parlio-MFA-Required": "1"},
            )

    def scope(self, tenant_id: str | None) -> str:
        """Resolve the organisation a dashboard query is about: the requested one (must be a
        membership) or the caller's first organisation. Never falls through to "all tenants"."""
        tid = tenant_id or (self.tenant_ids[0] if self.tenant_ids else None)
        if tid is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "no organisation")
        self.require_tenant(tid)
        return tid

    def require_admin(self, tenant_id: str) -> None:
        self.require_tenant(tenant_id)
        if self.role_in(tenant_id) not in ("owner", "admin"):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "admin role required")

    def require_staff(self, *roles: str) -> str:
        """Platform staff gate; ``roles`` narrows to specific staff roles (owner always passes)."""
        role = self.staff_role
        if role is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "platform staff only")
        if roles and role != "owner" and role not in roles:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, f"requires staff role: {', '.join(roles)}"
            )
        return role


class TokenError(ValueError):
    pass


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _split_jwt(token: str) -> tuple[dict[str, Any], Any, bytes, bytes]:
    try:
        head_b64, body_b64, sig_b64 = token.split(".")
        header = json.loads(_b64url_decode(head_b64))
        payload = json.loads(_b64url_decode(body_b64))
        sig = _b64url_decode(sig_b64)
    except (ValueError, UnicodeDecodeError) as e:  # includes json/base64 errors
        raise TokenError("malformed token") from e
    if not isinstance(header, dict):
        raise TokenError("malformed token")
    return header, payload, f"{head_b64}.{body_b64}".encode(), sig


def _check_claims(payload: Any, now: float | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise TokenError("bad signature")
    claims: dict[str, Any] = payload
    exp = claims.get("exp")
    if exp is not None and (now or time.time()) >= float(exp):
        raise TokenError("token expired")
    if not claims.get("sub") or not claims.get("email"):
        raise TokenError("token missing sub/email")
    return claims


def _verify_jwk(jwk: dict[str, Any], alg: str, signed: bytes, sig: bytes) -> bool:
    try:
        if jwk.get("kty") == "EC" and alg == "ES256":
            x = int.from_bytes(_b64url_decode(jwk["x"]), "big")
            y = int.from_bytes(_b64url_decode(jwk["y"]), "big")
            pub = ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1()).public_key()
            if len(sig) != 64:
                return False
            r, s_ = int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big")
            der = encode_dss_signature(r, s_)
            pub.verify(der, signed, ec.ECDSA(hashes.SHA256()))
            return True
        if jwk.get("kty") == "RSA" and alg == "RS256":
            n = int.from_bytes(_b64url_decode(jwk["n"]), "big")
            e = int.from_bytes(_b64url_decode(jwk["e"]), "big")
            pub_rsa = rsa.RSAPublicNumbers(e, n).public_key()
            pub_rsa.verify(sig, signed, padding.PKCS1v15(), hashes.SHA256())
            return True
    except (InvalidSignature, KeyError, ValueError):
        return False
    return False


class JwksCache:
    """Fetches and caches a project's JWKS; refreshes on unknown ``kid`` (key rotation)."""

    def __init__(self, url: str, ttl_s: float = 3600) -> None:
        self.url = url
        self.ttl_s = ttl_s
        self._keys: list[dict[str, Any]] = []
        self._fetched = 0.0

    async def keys(self, *, force: bool = False) -> list[dict[str, Any]]:
        if force or not self._keys or time.time() - self._fetched > self.ttl_s:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(self.url)
                r.raise_for_status()
                body = r.json()
            self._keys = list(body.get("keys", [])) if isinstance(body, dict) else []
            self._fetched = time.time()
        return self._keys


_jwks: dict[str, JwksCache] = {}


async def decode_supabase_jwt_jwks(
    token: str, jwks_url: str, now: float | None = None
) -> dict[str, Any]:
    """Verify an ES256/RS256 JWT against the project's published signing keys."""
    header, payload, signed, sig = _split_jwt(token)
    alg = str(header.get("alg"))
    if alg not in ("ES256", "RS256"):
        raise TokenError(f"unsupported alg {alg}")
    cache = _jwks.setdefault(jwks_url, JwksCache(jwks_url))
    kid = header.get("kid")
    try:
        keys = await cache.keys()
        match = [k for k in keys if kid is None or k.get("kid") == kid]
        if not match:
            keys = await cache.keys(force=True)
            match = [k for k in keys if kid is None or k.get("kid") == kid]
    except httpx.HTTPError as e:
        raise TokenError("signing keys unavailable") from e
    if not any(_verify_jwk(k, alg, signed, sig) for k in match):
        raise TokenError("bad signature")
    return _check_claims(payload, now)


def decode_supabase_jwt(token: str, secret: str, now: float | None = None) -> dict[str, Any]:
    """Verify an HS256 JWT (Supabase legacy JWT secret) and return its claims."""
    header, payload, signed, sig = _split_jwt(token)
    if header.get("alg") != "HS256":
        raise TokenError(f"unsupported alg {header.get('alg')}")
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, sig):
        raise TokenError("bad signature")
    return _check_claims(payload, now)


def encode_supabase_jwt(claims: dict[str, Any], secret: str) -> str:
    """Test helper: mint an HS256 token the way Supabase does."""

    def enc(o: dict[str, Any]) -> str:
        raw = json.dumps(o, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    head = enc({"alg": "HS256", "typ": "JWT"})
    body = enc(claims)
    sig = hmac.new(secret.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{base64.urlsafe_b64encode(sig).rstrip(b'=').decode()}"


async def _activate_invites(store: CallStore, email: str) -> list[Member]:
    """Pending invitations become active memberships on the invitee's first sign-in."""
    out: list[Member] = []
    for m in await store.memberships_for_email(email):
        if m.status == "invited":
            m = await store.upsert_member(m.model_copy(update={"status": "active"}))
        out.append(m)
    return out


async def with_mfa(
    p: Principal, security: SecurityService, request: Request | None, token: str | None
) -> Principal:
    session = await security.check_mfa(p.user_id, token)
    p.mfa_verified = session is not None
    p.mfa_session_id = session.id if session else None
    for m in p.memberships:
        if m.status == "active" and await security.mfa_required(m.tenant_id, m.role, p.user_id):
            p.mfa_required.append(m.tenant_id)
    if session is None and p.memberships and request is not None:
        with suppress(Exception):
            await security.touch(
                p.user_id,
                request.headers.get("user-agent"),
                request.client.host if request.client else None,
            )
    return p


async def current_user(
    request: Request,
    store: StoreDep,
    settings: SettingsDep,
    security: SecurityDep,
    admin: AdminDep,
    authorization: Annotated[str | None, Header()] = None,
    x_parlio_user: Annotated[str | None, Header()] = None,
    x_parlio_mfa: Annotated[str | None, Header()] = None,
    x_parlio_view_as: Annotated[str | None, Header()] = None,
) -> Principal:
    p = await resolve_user(store, settings, authorization, x_parlio_user)
    if p.staff_role is None and p.email in {e.lower() for e in settings.platform_owner_emails}:
        p.memberships.append(await admin.ensure_owner(p.email, p.user_id, p.name))
    p = await with_mfa(p, security, request, x_parlio_mfa)
    if x_parlio_view_as:
        grant = admin.verify_view_as(x_parlio_view_as)
        if grant is None or p.staff_role is None or grant.staff_email != p.email:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "view-as grant invalid or expired")
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "view-as-tenant is read-only")
        p.view_as = grant.tenant_id
        p.memberships.append(
            Member(
                tenant_id=grant.tenant_id,
                user_id=p.user_id,
                email=p.email,
                name=p.name,
                role="viewer",
                status="active",
            )
        )
    return p


async def resolve_user(
    store: CallStore,
    settings: Settings,
    authorization: str | None,
    x_parlio_user: str | None,
) -> Principal:
    if settings.auth_mode == "dev":
        email = (x_parlio_user or DEV_USER_EMAIL).lower()
        members = await _activate_invites(store, email)
        if not members and email == DEV_USER_EMAIL:
            members = [
                await store.upsert_member(
                    Member(tenant_id=DEV_TENANT, user_id="dev-owner", email=email, role="owner")
                )
            ]
        digest = hashlib.sha1(email.encode()).hexdigest()[:8]
        uid = members[0].user_id if members else f"dev-{digest}"
        return Principal(user_id=uid, email=email, memberships=members, mode="dev")

    if not settings.supabase_jwt_secret and not settings.supabase_url:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "auth not configured")
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    token = authorization[7:].strip()
    try:
        header, _, _, _ = _split_jwt(token)
        if header.get("alg") == "HS256":
            if not settings.supabase_jwt_secret:
                raise TokenError("HS256 token but PARLIO_SUPABASE_JWT_SECRET not set")
            claims = decode_supabase_jwt(token, settings.supabase_jwt_secret)
        else:
            if not settings.supabase_url:
                raise TokenError("asymmetric token but PARLIO_SUPABASE_URL not set")
            jwks_url = settings.supabase_url.rstrip("/") + "/auth/v1/.well-known/jwks.json"
            claims = await decode_supabase_jwt_jwks(token, jwks_url)
    except TokenError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(e)) from e
    email = str(claims["email"]).lower()
    meta = claims.get("user_metadata") or {}
    activated = await _activate_invites(store, email)
    return Principal(
        user_id=str(claims["sub"]),
        email=email,
        name=meta.get("full_name") or meta.get("name"),
        memberships=activated,
        mode="supabase",
    )


UserDep = Annotated[Principal, Depends(current_user)]
