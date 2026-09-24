"""Phase 16b: platform-owner admin console."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from parlio_api.admin import PLATFORM_TENANT, AdminService
from parlio_api.auth import DEV_TENANT
from parlio_api.billing import PLAN_BY_ID, BillingService, SubscriptionStatus
from parlio_api.observability import RateLimiter
from parlio_api.store import Member

from .test_platform import _run_call

OWNER = {"X-Parlio-User": "owner@demo.parlio.local"}  # bootstrapped platform owner (dev)
TENANT_USER = {"X-Parlio-User": "intruder@other.example"}
Q = {"tenant_id": DEV_TENANT}


async def _staff(app: FastAPI, email: str, role: str) -> dict[str, str]:
    admin: AdminService = app.state.admin
    await admin.invite_staff(email, role, None)  # type: ignore[arg-type]
    return {"X-Parlio-User": email}


async def test_admin_requires_platform_staff(client: AsyncClient, app: FastAPI) -> None:
    # an ordinary tenant owner is not staff
    store = app.state.store
    await store.upsert_member(
        Member(tenant_id="acme", user_id="u-acme", email="intruder@other.example", role="owner")
    )
    r = await client.get("/v1/admin/overview", headers=TENANT_USER)
    assert r.status_code == 403
    # anonymous dev user with no memberships
    r = await client.get("/v1/admin/tenants", headers={"X-Parlio-User": "nobody@x.example"})
    assert r.status_code == 403
    # bootstrapped owner passes and sees staff role on /me
    me = await client.get("/v1/me", headers=OWNER)
    assert me.json()["staff_role"] == "owner"
    assert all(m["tenant_id"] != PLATFORM_TENANT for m in me.json()["memberships"])
    r = await client.get("/v1/admin/overview", headers=OWNER)
    assert r.status_code == 200, r.text
    assert r.json()["staff"] == 1


async def test_platform_membership_is_not_a_tenant(client: AsyncClient) -> None:
    """Staff must not be able to use tenant-scoped routes for the reserved platform org."""
    r = await client.get("/v1/billing/usage", params={"tenant_id": PLATFORM_TENANT}, headers=OWNER)
    assert r.status_code == 403
    # ...and staff have no implicit access to any customer tenant
    r = await client.get("/v1/billing/usage", params={"tenant_id": "acme"}, headers=OWNER)
    assert r.status_code == 403


async def test_staff_roles_and_readonly(client: AsyncClient, app: FastAPI) -> None:
    ro = await _staff(app, "ro@parlio.example", "readonly")
    sup = await _staff(app, "sup@parlio.example", "support")
    fin = await _staff(app, "fin@parlio.example", "finance")
    # read works for everyone
    for h in (ro, sup, fin):
        assert (await client.get("/v1/admin/tenants", headers=h)).status_code == 200
    # readonly blocked from writes
    r = await client.post(f"/v1/admin/tenants/{DEV_TENANT}/notes", json={"text": "hi"}, headers=ro)
    assert r.status_code == 403
    # support can't change plans; finance can
    body = {"plan_id": "growth"}
    r = await client.post(
        f"/v1/admin/tenants/{DEV_TENANT}/subscription/plan", json=body, headers=sup
    )
    assert r.status_code == 403
    r = await client.post(
        f"/v1/admin/tenants/{DEV_TENANT}/subscription/plan", json=body, headers=fin
    )
    assert r.status_code == 200, r.text
    assert r.json()["plan_id"] == "growth"
    # only owners manage staff
    r = await client.post(
        "/v1/admin/staff", json={"email": "x@parlio.example", "role": "support"}, headers=fin
    )
    assert r.status_code == 403
    r = await client.post(
        "/v1/admin/staff", json={"email": "x@parlio.example", "role": "support"}, headers=OWNER
    )
    assert r.status_code == 201
    uid = r.json()["user_id"]
    r = await client.patch(f"/v1/admin/staff/{uid}", json={"role": "finance"}, headers=OWNER)
    assert r.json()["role"] == "finance"
    assert (await client.delete(f"/v1/admin/staff/{uid}", headers=OWNER)).status_code == 204
    staff = (await client.get("/v1/admin/staff", headers=OWNER)).json()
    assert {m["email"] for m in staff} == {
        "owner@demo.parlio.local",
        "ro@parlio.example",
        "sup@parlio.example",
        "fin@parlio.example",
    }


async def test_ip_allowlist(client: AsyncClient) -> None:
    # httpx ASGI transport reports client 127.0.0.1
    r = await client.put(
        "/v1/admin/staff/settings", json={"ip_allowlist": ["10.0.0.0/8"]}, headers=OWNER
    )
    assert r.status_code == 200, r.text
    # the owner's own IP is appended so the request that set the list never locks them out
    assert "127.0.0.1/32" in r.json()["ip_allowlist"]
    r = await client.put(
        "/v1/admin/staff/settings",
        json={"ip_allowlist": ["10.0.0.0/8", "127.0.0.1/32"]},
        headers=OWNER,
    )
    assert r.status_code == 200
    assert (await client.get("/v1/admin/overview", headers=OWNER)).status_code == 200
    r = await client.put("/v1/admin/staff/settings", json={"ip_allowlist": ["bad"]}, headers=OWNER)
    assert r.status_code == 400


async def test_mfa_mandatory_for_staff_outside_dev(
    client: AsyncClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    from parlio_api.auth import encode_supabase_jwt
    from parlio_api.settings import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "auth_mode", "supabase")
    monkeypatch.setattr(settings, "supabase_jwt_secret", "s3cret")
    monkeypatch.setattr(settings, "platform_owner_emails", ["boss@parlio.example"])
    tok = encode_supabase_jwt({"sub": "u-boss", "email": "boss@parlio.example"}, "s3cret")
    r = await client.get("/v1/admin/overview", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 403
    assert r.headers.get("X-Parlio-MFA-Required") == "1"


async def test_tenant_directory_detail_and_isolation(client: AsyncClient, app: FastAPI) -> None:
    await _run_call(client, "c1", duration_s=90)
    store = app.state.store
    await store.upsert_member(Member(tenant_id="acme", user_id="u1", email="a@acme.example"))
    billing: BillingService = app.state.billing
    await billing.subscription("acme")
    r = await client.get("/v1/admin/tenants", headers=OWNER)
    assert r.status_code == 200
    ids = {t["tenant_id"] for t in r.json()}
    assert {DEV_TENANT, "acme"} <= ids and PLATFORM_TENANT not in ids
    r = await client.get("/v1/admin/tenants", params={"q": "acme"}, headers=OWNER)
    assert [t["tenant_id"] for t in r.json()] == ["acme"]
    r = await client.get(f"/v1/admin/tenants/{DEV_TENANT}", headers=OWNER)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["summary"]["calls_period"] == 1
    assert d["subscription"]["plan_id"]
    assert d["assistants"][0]["id"]
    assert (
        await client.get(f"/v1/admin/tenants/{PLATFORM_TENANT}", headers=OWNER)
    ).status_code == 404
    csv = await client.get("/v1/admin/export/tenants.csv", headers=OWNER)
    assert csv.status_code == 200 and "tenant_id" in csv.text and DEV_TENANT in csv.text


async def test_subscription_lifecycle_credits_refunds_limits(
    client: AsyncClient, app: FastAPI
) -> None:
    t = DEV_TENANT
    base = f"/v1/admin/tenants/{t}"
    # enterprise is staff-only: tenant route rejects, admin route accepts
    r = await client.post(
        "/v1/billing/subscription", params=Q, json={"plan_id": "enterprise"}, headers=OWNER
    )
    assert r.status_code == 422  # dev owner is also the demo tenant owner: tenant path refuses
    r = await client.post(
        f"{base}/subscription/plan", json={"plan_id": "enterprise"}, headers=OWNER
    )
    assert r.status_code == 200, r.text
    r = await client.post(f"{base}/subscription/plan", json={"plan_id": "scale"}, headers=OWNER)
    assert r.json()["plan_id"] == "scale"
    # trial extend / convert
    r = await client.post(f"{base}/subscription/trial/extend", json={"days": 7}, headers=OWNER)
    assert r.status_code == 200 and r.json()["status"] == "trialing"
    r = await client.post(f"{base}/subscription/trial/convert", headers=OWNER)
    assert r.json()["status"] == "active" and r.json()["trial_ends_at"] is None
    # pause -> tenant can no longer be served; reactivate
    r = await client.post(
        f"{base}/subscription/status", json={"status": "paused", "reason": "holiday"}, headers=OWNER
    )
    assert r.json()["status"] == "paused" and r.json()["status_reason"] == "holiday"
    billing: BillingService = app.state.billing
    assert await billing.concurrent_limit(t) == 0
    r = await client.post(f"{base}/subscription/status", json={"status": "active"}, headers=OWNER)
    assert r.json()["status"] == "active"
    assert await billing.concurrent_limit(t) > 0
    r = await client.post(f"{base}/subscription/status", json={"status": "trialing"}, headers=OWNER)
    assert r.status_code == 400
    # credits reduce estimated total; balance visible in usage
    r = await client.post(
        f"{base}/credits", json={"pence": 2500, "reason": "goodwill"}, headers=OWNER
    )
    assert r.status_code == 201
    u = await billing.usage(t)
    assert u.credit_balance_pence == 2500
    assert u.estimated_total_pence == max(0, PLAN_BY_ID["scale"].monthly_pence - 2500)
    # refund (simulated provider) and invoices
    r = await client.post(f"{base}/refunds", json={"pence": 500, "reason": "dup"}, headers=OWNER)
    assert r.status_code == 201 and r.json()["provider_ref"]
    assert (await client.get(f"{base}/invoices", headers=OWNER)).status_code == 200
    # limits: concurrency cap + rate-limit override applied to the limiter
    r = await client.put(
        f"{base}/limits",
        json={"max_concurrent_calls": 2, "minutes_cap": 100, "rate_limit_per_minute": 20},
        headers=OWNER,
    )
    assert r.status_code == 200, r.text
    limiter: RateLimiter = app.state.rate_limiter
    assert limiter.overrides[f"tenant:{t}"] == 20
    assert await billing.concurrent_limit(t) == 2
    # every action landed in the tenant's audit log, flagged as platform staff
    admin: AdminService = app.state.admin
    acts = await admin.staff_activity()
    names = {a.action for a in acts}
    assert {
        "admin.subscription.plan",
        "admin.subscription.trial_extend",
        "admin.subscription.paused",
        "admin.credit.grant",
        "admin.refund",
        "admin.limits.set",
    } <= names
    assert all(a.meta.get("platform_staff") is True for a in acts)
    # suspend / cancel
    r = await client.post(
        f"{base}/subscription/status", json={"status": "suspended"}, headers=OWNER
    )
    assert r.json()["status"] == "suspended"
    r = await client.post(
        f"{base}/subscription/status", json={"status": "cancelled"}, headers=OWNER
    )
    assert r.json()["status"] == "cancelled"


async def test_plan_and_coupon_catalogue(client: AsyncClient, app: FastAPI) -> None:
    plans = (await client.get("/v1/admin/plans", headers=OWNER)).json()
    growth = next(p for p in plans if p["id"] == "growth")
    growth["monthly_pence"] = 20900
    r = await client.put("/v1/admin/plans/growth", json=growth, headers=OWNER)
    assert r.status_code == 200 and r.json()["monthly_pence"] == 20900
    assert PLAN_BY_ID["growth"].monthly_pence == 20900
    # tenant-facing catalogue reflects it
    pub = await client.get("/v1/billing/plans", params=Q, headers=OWNER)
    assert pub.status_code in (200, 403)
    new = {**growth, "id": "agency", "name": "Agency", "monthly_pence": 50000}
    r = await client.put("/v1/admin/plans/agency", json=new, headers=OWNER)
    assert r.status_code == 200 and "agency" in PLAN_BY_ID
    r = await client.delete("/v1/admin/plans/agency", headers=OWNER)
    assert r.status_code == 200 and "agency" not in PLAN_BY_ID
    r = await client.delete("/v1/admin/plans/growth", headers=OWNER)
    assert r.status_code == 200 and r.json()["monthly_pence"] != 20900  # built-ins revert
    await app.state.billing.subscription(DEV_TENANT)  # demo is on starter
    r = await client.delete("/v1/admin/plans/starter", headers=OWNER)
    assert r.status_code == 400  # in use by the demo tenant
    # persisted edits survive a reload
    r = await client.put("/v1/admin/plans/growth", json=growth, headers=OWNER)
    admin: AdminService = app.state.admin
    PLAN_BY_ID["growth"] = PLAN_BY_ID["growth"].model_copy(update={"monthly_pence": 1})
    await admin.load()
    assert PLAN_BY_ID["growth"].monthly_pence == 20900
    coupon: dict[str, Any] = {
        "code": "LAUNCH50",
        "percent_off": 50,
        "months": 3,
        "description": "launch",
    }
    r = await client.put("/v1/admin/coupons/LAUNCH50", json=coupon, headers=OWNER)
    assert r.status_code == 200, r.text
    assert any(
        c["code"] == "LAUNCH50"
        for c in (await client.get("/v1/admin/coupons", headers=OWNER)).json()
    )
    r = await client.post(
        f"/v1/admin/tenants/{DEV_TENANT}/subscription/plan",
        json={"plan_id": "growth", "coupon_code": "LAUNCH50"},
        headers=OWNER,
    )
    assert r.status_code == 200 and r.json()["coupon"] == "LAUNCH50"
    assert (await client.delete("/v1/admin/coupons/LAUNCH50", headers=OWNER)).status_code == 204
    # restore module-level catalogue state for other tests
    r = await client.post(
        f"/v1/admin/tenants/{DEV_TENANT}/subscription/plan",
        json={"plan_id": "starter"},
        headers=OWNER,
    )
    assert r.status_code == 200
    assert (await client.delete("/v1/admin/plans/growth", headers=OWNER)).status_code == 200
    assert PLAN_BY_ID["growth"].monthly_pence == 23900


async def test_flags_notes_status_and_view_as(client: AsyncClient, app: FastAPI) -> None:
    base = f"/v1/admin/tenants/{DEV_TENANT}"
    r = await client.put(f"{base}/flags", json={"flags": {"browser_voice": True}}, headers=OWNER)
    assert r.status_code == 200 and r.json()["flags"] == {"browser_voice": True}
    r = await client.put(f"{base}/flags", json={"flags": {"nope": True}}, headers=OWNER)
    assert r.status_code == 400
    r = await client.post(f"{base}/notes", json={"text": "VIP", "pinned": True}, headers=OWNER)
    assert r.status_code == 201
    nid = r.json()["id"]
    detail = (await client.get(base, headers=OWNER)).json()
    assert detail["notes"][0]["text"] == "VIP" and detail["flags"]["flags"]["browser_voice"]
    assert (await client.delete(f"{base}/notes/{nid}", headers=OWNER)).status_code == 204
    # status banner: public endpoint shows it only while active
    assert (await client.get("/v1/public/status")).json()["active"] is False
    r = await client.put(
        "/v1/admin/status",
        json={"level": "incident", "title": "SMS delays", "message": "Investigating"},
        headers=OWNER,
    )
    assert r.status_code == 200
    pub = (await client.get("/v1/public/status")).json()
    assert pub["active"] is True and pub["level"] == "incident"
    # view-as: read-only grant lets staff read tenant routes, never write
    r = await client.post(f"{base}/view-as", headers=OWNER)
    assert r.status_code == 200
    tok = r.json()["token"]
    h = {**OWNER, "X-Parlio-View-As": tok}
    assert (await client.get("/v1/billing/usage", params=Q, headers=h)).status_code == 200
    me = (await client.get("/v1/me", headers=h)).json()
    assert me["view_as"] == DEV_TENANT
    r = await client.post(
        "/v1/billing/subscription", params=Q, json={"plan_id": "growth"}, headers=h
    )
    assert r.status_code == 403
    # grant is bound to the issuing staff member and can't be used by a tenant user
    r = await client.get(
        "/v1/billing/usage", params=Q, headers={**TENANT_USER, "X-Parlio-View-As": tok}
    )
    assert r.status_code == 403
    assert (
        await client.get("/v1/billing/usage", params=Q, headers={**OWNER, "X-Parlio-View-As": "x"})
    ).status_code == 403
    admin: AdminService = app.state.admin
    assert "admin.view_as" in {a.action for a in await admin.staff_activity()}


async def test_cross_tenant_analytics(client: AsyncClient, app: FastAPI) -> None:
    await _run_call(client, "c1", duration_s=120)
    await _run_call(client, "c2", duration_s=30, answer_latency_s=2.0)
    r = await client.get("/v1/admin/analytics", params={"days": 7}, headers=OWNER)
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["demand"]["calls"] == 2 and a["demand"]["minutes"] >= 2.5
    assert a["demand"]["inbound"] == 2
    assert a["business"]["tenants"] >= 1 and a["business"]["mrr_pence"] >= 0
    assert a["quality"]["answer_latency_p50_s"] is not None
    assert sum(p["value"] for p in a["demand"]["calls_by_day"]) == 2
    assert len(a["demand"]["calls_by_hour"]) == 24
    csv = await client.get("/v1/admin/export/analytics.csv", params={"days": 7}, headers=OWNER)
    assert csv.status_code == 200 and csv.text.splitlines()[0].startswith("day,")


async def test_plan_entitlements_gate_features(client: AsyncClient, app: FastAPI) -> None:
    billing: BillingService = app.state.billing
    plans = (await client.get("/v1/admin/plans", headers=OWNER)).json()
    starter = next(p for p in plans if p["id"] == "starter")
    assert "outbound" not in starter["entitlements"]
    growth = next(p for p in plans if p["id"] == "growth")
    assert {"calendar_booking", "warm_transfers", "ask_ai", "languages"} <= set(
        growth["entitlements"]
    )
    # unknown keys are rejected
    r = await client.put(
        "/v1/admin/plans/starter",
        json={**starter, "entitlements": ["teleport"]},
        headers=OWNER,
    )
    assert r.status_code == 400 and "teleport" in r.text
    # trials unlock everything; a converted starter tenant is gated
    ent = (await client.get("/v1/billing/entitlements", params=Q)).json()
    assert ent["enabled"]["outbound"] is True and "outbound" in ent["catalogue"]
    await billing.set_status(DEV_TENANT, SubscriptionStatus.ACTIVE)
    try:
        ent = (await client.get("/v1/billing/entitlements", params=Q)).json()
        assert ent["enabled"]["outbound"] is False and ent["enabled"]["browser_voice"] is True
        r = await client.post(
            "/v1/outbound/leads", params=Q, json={"phone": "+447700900123", "source": "web"}
        )
        assert r.status_code == 403 and "not included in your plan" in r.text
        r = await client.post("/v1/connectors", params=Q, json={"provider": "hubspot"})
        assert r.status_code == 403
        # owner adds outbound to Starter -> unlocked
        r = await client.put(
            "/v1/admin/plans/starter",
            json={**starter, "entitlements": [*starter["entitlements"], "outbound"]},
            headers=OWNER,
        )
        assert r.status_code == 200 and "outbound" in r.json()["entitlements"]
        assert await billing.entitled(DEV_TENANT, "outbound") is True
        # per-tenant flag override switches it off again / turns on another
        r = await client.put(
            f"/v1/admin/tenants/{DEV_TENANT}/flags",
            json={"flags": {"outbound": False, "simulation": True}},
            headers=OWNER,
        )
        assert r.status_code == 200
        ent = await billing.entitlements(DEV_TENANT)
        assert ent["outbound"] is False and ent["simulation"] is True
        # tenant users cannot edit plans
        r = await client.put("/v1/admin/plans/starter", json=starter, headers=TENANT_USER)
        assert r.status_code == 403
    finally:
        await client.put(f"/v1/admin/tenants/{DEV_TENANT}/flags", json={"flags": {}}, headers=OWNER)
        await client.put("/v1/admin/plans/starter", json=starter, headers=OWNER)
        await billing.set_status(DEV_TENANT, SubscriptionStatus.TRIALING)
    assert "outbound" not in PLAN_BY_ID["starter"].entitlements


async def test_plan_trial_days(client: AsyncClient, app: FastAPI) -> None:
    from datetime import timedelta

    billing: BillingService = app.state.billing
    d = (await client.get("/v1/admin/plans/defaults", headers=OWNER)).json()
    assert d["trial_days"] == billing.trial_days
    plans = (await client.get("/v1/admin/plans", headers=OWNER)).json()
    starter = next(p for p in plans if p["id"] == "starter")
    growth = next(p for p in plans if p["id"] == "growth")
    assert starter["trial_days"] is None  # falls back to the platform default
    assert billing.trial_days_for("starter") == billing.trial_days
    try:
        r = await client.put(
            "/v1/admin/plans/starter", json={**starter, "trial_days": 400}, headers=OWNER
        )
        assert r.status_code == 422
        r = await client.put(
            "/v1/admin/plans/starter", json={**starter, "trial_days": 30}, headers=OWNER
        )
        assert r.status_code == 200 and r.json()["trial_days"] == 30
        assert billing.trial_days_for("starter") == 30
        r = await client.put(
            "/v1/admin/plans/growth", json={**growth, "trial_days": 0}, headers=OWNER
        )
        assert r.status_code == 200
        # new tenants get the starter plan's trial length
        sub = await billing.subscription("trial-tenant")
        assert sub.status == SubscriptionStatus.TRIALING and sub.trial_ends_at is not None
        assert abs((sub.trial_ends_at - sub.created_at) - timedelta(days=30)) < timedelta(minutes=1)
        # switching to a plan with no trial during the trial ends it
        sub = await billing.change_plan("trial-tenant", "growth")
        assert sub.status == SubscriptionStatus.ACTIVE and sub.trial_ends_at is None
        # onboarding recommendation reflects the plan's trial length
        r = await client.post("/v1/onboarding/recommend", json={"monthly_calls": "50-200"})
        assert r.status_code == 200
        rec = r.json()
        plan = PLAN_BY_ID[rec["plan_id"]]
        assert rec["trial_days"] == (
            plan.trial_days if plan.trial_days is not None else billing.trial_days
        )
    finally:
        await client.put("/v1/admin/plans/starter", json=starter, headers=OWNER)
        await client.put("/v1/admin/plans/growth", json=growth, headers=OWNER)
    assert PLAN_BY_ID["starter"].trial_days is None


async def test_tenant_usage_hides_vendor_cost(client: AsyncClient, app: FastAPI) -> None:
    await app.state.store.upsert_member(
        Member(tenant_id="acme", user_id="u-acme", email="intruder@other.example", role="owner")
    )
    r = await client.get("/v1/billing/usage", params={"tenant_id": "acme"}, headers=TENANT_USER)
    assert r.status_code == 200
    body = r.json()
    assert body["vendor_cost_pence"] == 0
    assert body["gross_margin_pct"] is None
    assert all(c["vendor_pence"] == 0 for c in body["top_calls"])
    r = await client.get("/v1/billing/usage", params=Q, headers=OWNER)
    assert r.status_code == 200
    assert r.json()["gross_margin_pct"] is not None
