"""Plan caps (engineers / locations / dashboard users) and the Phase 20-22 entitlements: plan
defaults, platform-owner edits, per-tenant overrides, and enforcement at the add points."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from parlio_api.auth import DEV_TENANT
from parlio_api.billing import PLAN_BY_ID, BillingService, SubscriptionStatus

from .test_admin import OWNER
from .test_team_scheduling import _res

pytestmark = pytest.mark.anyio

Q = {"tenant_id": DEV_TENANT}


def test_builtin_plan_caps() -> None:
    assert (PLAN_BY_ID["starter"].max_resources, PLAN_BY_ID["starter"].max_sites) == (1, 1)
    assert PLAN_BY_ID["starter"].max_members == 2
    assert PLAN_BY_ID["growth"].max_resources == 5 and PLAN_BY_ID["scale"].max_resources == 25
    assert PLAN_BY_ID["enterprise"].max_resources == 0  # unlimited
    assert "team_scheduling" not in PLAN_BY_ID["starter"].entitlements
    assert "team_scheduling" in PLAN_BY_ID["growth"].entitlements
    assert "scheduling_tool" not in PLAN_BY_ID["growth"].entitlements
    assert "scheduling_tool" in PLAN_BY_ID["scale"].entitlements


async def test_caps_enforced_and_owner_editable(client: AsyncClient, app: FastAPI) -> None:
    billing: BillingService = app.state.billing
    plans = (await client.get("/v1/admin/plans", headers=OWNER)).json()
    starter = next(p for p in plans if p["id"] == "starter")
    assert starter["max_resources"] == 1 and starter["max_members"] == 2
    await billing.set_status(DEV_TENANT, SubscriptionStatus.ACTIVE)  # starter, converted
    try:
        # Starter has no team scheduling at all
        r = await client.post("/v1/team/resources", params=Q, json=_res("Ann"))
        assert r.status_code == 403 and "not included in your plan" in r.text
        r = await client.put(
            "/v1/team/scheduler", params=Q, json={"provider": "simulated", "name": "Tool"}
        )
        assert r.status_code == 403
        # second location needs multi_location
        sites = [{"id": "leeds", "name": "Leeds", "numbers": []}]
        r = await client.put(f"/v1/assistants/{DEV_TENANT}/sites", json=sites)
        assert r.status_code == 200, r.text
        two = [*sites, {"id": "york", "name": "York", "numbers": []}]
        r = await client.put(f"/v1/assistants/{DEV_TENANT}/sites", json=two)
        assert r.status_code == 403 and "Multiple locations" in r.text
        # transcript search is Growth+
        r = await client.get("/v1/calls/search", params={**Q, "q": "boiler"})
        assert r.status_code == 403

        # owner enables team scheduling on Starter but keeps the 1-engineer cap
        r = await client.put(
            "/v1/admin/plans/starter",
            json={**starter, "entitlements": [*starter["entitlements"], "team_scheduling"]},
            headers=OWNER,
        )
        assert r.status_code == 200
        r = await client.post("/v1/team/resources", params=Q, json=_res("Ann"))
        assert r.status_code == 201, r.text
        ann = r.json()["id"]
        r = await client.post("/v1/team/resources", params=Q, json=_res("Bob"))
        assert r.status_code == 403 and "1 team members" in r.text and "upgrade" in r.text
        # inactive additions are free; reactivating counts
        r = await client.post("/v1/team/resources", params=Q, json=_res("Bob", active=False))
        assert r.status_code == 201, r.text
        bob = r.json()["id"]
        r = await client.put(f"/v1/team/resources/{bob}", params=Q, json=_res("Bob"))
        assert r.status_code == 403
        # editing / deactivating an existing engineer is always allowed
        r = await client.put(
            f"/v1/team/resources/{ann}", params=Q, json=_res("Ann Smith", active=False)
        )
        assert r.status_code == 200, r.text
        r = await client.put(f"/v1/team/resources/{bob}", params=Q, json=_res("Bob"))
        assert r.status_code == 200, r.text

        # tenant override lifts the cap; 0 = unlimited
        r = await client.put(
            f"/v1/admin/tenants/{DEV_TENANT}/limits", json={"max_resources": 0}, headers=OWNER
        )
        assert r.status_code == 200 and r.json()["max_resources"] == 0
        r = await client.put(f"/v1/team/resources/{ann}", params=Q, json=_res("Ann"))
        assert r.status_code == 200
        r = await client.post("/v1/team/resources", params=Q, json=_res("Cat"))
        assert r.status_code == 201

        # owner raises the plan cap itself
        r = await client.put(
            "/v1/admin/plans/starter", json={**starter, "max_sites": 3}, headers=OWNER
        )
        assert r.status_code == 200 and r.json()["max_sites"] == 3
        assert await billing.cap(DEV_TENANT, "max_sites") == 3
        r = await client.put(
            "/v1/admin/plans/starter", json={**starter, "max_sites": -1}, headers=OWNER
        )
        assert r.status_code == 422

        # dashboard users: starter allows 2 (owner + 1)
        members = (await client.get(f"/v1/organisations/{DEV_TENANT}/members")).json()
        base = len(members)
        r = await client.put(
            f"/v1/admin/tenants/{DEV_TENANT}/limits",
            json={"max_resources": 0, "max_members": base + 1},
            headers=OWNER,
        )
        assert r.status_code == 200
        invite: dict[str, Any] = {"email": "new1@demo.example", "role": "admin"}
        r = await client.post(f"/v1/organisations/{DEV_TENANT}/members", json=invite)
        assert r.status_code == 201, r.text
        r = await client.post(
            f"/v1/organisations/{DEV_TENANT}/members",
            json={"email": "new2@demo.example", "role": "admin"},
        )
        assert r.status_code == 403 and "dashboard users" in r.text

        # tenant sees usage vs cap
        ent = (await client.get("/v1/billing/entitlements", params=Q)).json()
        assert ent["caps"]["max_members"]["used"] == base + 1
        assert ent["caps"]["max_members"]["limit"] == base + 1
        assert ent["caps"]["max_resources"]["limit"] == 0
    finally:
        await client.put("/v1/admin/plans/starter", json=starter, headers=OWNER)
        await client.put(f"/v1/admin/tenants/{DEV_TENANT}/limits", json={}, headers=OWNER)
        await billing.set_status(DEV_TENANT, SubscriptionStatus.TRIALING)
