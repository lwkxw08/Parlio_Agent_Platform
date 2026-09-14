"""Dashboard authentication.

Two modes, selected by `PARLIO_AUTH_MODE`:

* ``dev`` (default) - no credentials required. The principal is the seeded demo owner, or the
  email given in ``X-Parlio-User`` (handy for multi-user testing). Never enable in production.
* ``supabase`` - ``Authorization: Bearer <access_token>`` issued by Supabase Auth (email/password
  or Google OAuth in the dashboard). Tokens are HS256 JWTs signed with the project's JWT secret,
  verified locally so no round-trip to Supabase is needed per request.

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

from fastapi import Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from parlio_api.deps import SecurityDep, SettingsDep, StoreDep
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

    @property
    def tenant_ids(self) -> list[str]:
        return [m.tenant_id for m in self.memberships if m.status == "active"]

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

    def require_admin(self, tenant_id: str) -> None:
        self.require_tenant(tenant_id)
        if self.role_in(tenant_id) not in ("owner", "admin"):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "admin role required")


class TokenError(ValueError):
    pass


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def decode_supabase_jwt(token: str, secret: str, now: float | None = None) -> dict[str, Any]:
    """Verify an HS256 JWT (Supabase access token) and return its claims."""
    try:
        head_b64, body_b64, sig_b64 = token.split(".")
        header = json.loads(_b64url_decode(head_b64))
        payload = json.loads(_b64url_decode(body_b64))
        sig = _b64url_decode(sig_b64)
    except (ValueError, UnicodeDecodeError) as e:  # includes json/base64 errors
        raise TokenError("malformed token") from e
    if header.get("alg") != "HS256":
        raise TokenError(f"unsupported alg {header.get('alg')}")
    signed = f"{head_b64}.{body_b64}".encode()
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).digest()
    if not isinstance(payload, dict) or not hmac.compare_digest(expected, sig):
        raise TokenError("bad signature")
    claims: dict[str, Any] = payload
    exp = claims.get("exp")
    if exp is not None and (now or time.time()) >= float(exp):
        raise TokenError("token expired")
    if not claims.get("sub") or not claims.get("email"):
        raise TokenError("token missing sub/email")
    return claims


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
    authorization: Annotated[str | None, Header()] = None,
    x_parlio_user: Annotated[str | None, Header()] = None,
    x_parlio_mfa: Annotated[str | None, Header()] = None,
) -> Principal:
    p = await resolve_user(store, settings, authorization, x_parlio_user)
    return await with_mfa(p, security, request, x_parlio_mfa)


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

    if not settings.supabase_jwt_secret:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "auth not configured")
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    try:
        claims = decode_supabase_jwt(authorization[7:].strip(), settings.supabase_jwt_secret)
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
