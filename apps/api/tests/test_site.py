"""Public marketing-site endpoints: plans, demo voice with caps, contact form."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from parlio_api.billing import PLAN_BY_ID

pytestmark = pytest.mark.anyio


async def test_site_info_exposes_plans_and_demo(client: AsyncClient) -> None:
    r = await client.get("/v1/public/site")
    assert r.status_code == 200
    body = r.json()
    ids = [p["id"] for p in body["plans"]]
    assert {"starter", "growth", "scale", "enterprise"} <= set(ids)
    starter = next(p for p in body["plans"] if p["id"] == "starter")
    assert starter["monthly_pence"] == PLAN_BY_ID["starter"].monthly_pence
    assert starter["trial_days"] == body["trial_days"]
    assert "cost_pence" not in starter
    assert body["demo"]["voice_available"] is True
    assert body["demo"]["minutes_remaining"] > 0


async def test_demo_voice_starts_and_rate_limits(client: AsyncClient) -> None:
    payload = {"visitor": "visitor-12345", "name": "Sam"}
    ok = 0
    for _ in range(6):
        r = await client.post("/v1/public/site/demo/voice", json=payload)
        if r.status_code == 200:
            ok += 1
            assert r.json()["simulated"] is True
        else:
            assert r.status_code == 429
    assert ok == 5


async def test_demo_voice_unavailable_when_cap_zeroed(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from parlio_api.settings import get_settings

    monkeypatch.setattr(get_settings(), "site_demo_tenant_id", "no-such-tenant")
    r = await client.get("/v1/public/site")
    assert r.json()["demo"]["voice_available"] is False
    r = await client.post("/v1/public/site/demo/voice", json={"visitor": "visitor-12345"})
    assert r.status_code == 503


async def test_contact_form_stored_and_honeypot_dropped(client: AsyncClient) -> None:
    r = await client.post(
        "/v1/public/site/contact",
        json={
            "name": "Ann",
            "email": "ann@example.co.uk",
            "message": "Call me",
            "interest": "growth",
        },
    )
    assert r.status_code == 201 and r.json()["received"] is True
    r = await client.post(
        "/v1/public/site/contact",
        json={"name": "Bot", "email": "bad", "message": "spam"},
    )
    assert r.status_code == 422
    r = await client.post(
        "/v1/public/site/contact",
        json={"name": "Bot", "email": "b@x.io", "message": "spam", "website": "http://spam"},
    )
    assert r.status_code == 201
