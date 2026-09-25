"""No-card trial lifecycle: reminders -> grace -> paused -> closed, and how it interacts with
Checkout, staff overrides, call admission and the read-only dashboard wall.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from httpx import AsyncClient

from parlio_api.billing import (
    TRIAL_CLOSED_REASON,
    TRIAL_GRACE_REASON,
    TRIAL_PAUSED_REASON,
    UNAVAILABLE_NOTICE,
    BillingService,
    Subscription,
    SubscriptionStatus,
)

from .test_api import HEADERS

DEMO = {"tenant_id": "demo"}


async def _sub(app: FastAPI) -> Subscription:
    billing: BillingService = app.state.billing
    return await billing.subscription("demo")


async def _set_trial_end(app: FastAPI, ends: datetime) -> None:
    billing: BillingService = app.state.billing
    sub = await billing.subscription("demo")
    await billing._save(sub.model_copy(update={"trial_ends_at": ends}))


async def _admit(client: AsyncClient, number: str, call_id: str) -> dict[str, object]:
    r = await client.post(
        "/v1/worker/telephony/admit",
        params={"number": number, "call_id": call_id},
        headers=HEADERS,
    )
    assert r.status_code == 200, r.text
    return dict(r.json())


async def test_fresh_trial_has_no_card_and_is_serving(client: AsyncClient, app: FastAPI) -> None:
    sub = await _sub(app)
    assert sub.status == SubscriptionStatus.TRIALING
    assert sub.subscription_ref is None and sub.unpaid_trial
    assert sub.trial_notices == []
    billing: BillingService = app.state.billing
    assert await billing.serving("demo")
    r = await client.get("/v1/billing/trial", params=DEMO)
    assert r.status_code == 200
    assert r.json()["state"] == "trialing" and r.json()["calls_answered"] is True


async def test_reminders_fire_once_each_and_sweep_walks_the_lifecycle(
    client: AsyncClient, app: FastAPI
) -> None:
    billing: BillingService = app.state.billing
    seen: list[tuple[str, str]] = []

    async def on_event(sub: Subscription, event: str) -> None:
        seen.append((sub.tenant_id, event))

    billing.on_trial_event = on_event
    now = datetime(2026, 1, 1, 12, tzinfo=UTC)
    ends = now + timedelta(days=14)
    await _set_trial_end(app, ends)

    # day 1: nothing due
    assert await billing.sweep_trials(now) == []
    # T-7: one reminder; running the sweep again is a no-op
    assert await billing.sweep_trials(ends - timedelta(days=7)) == [("demo", "reminder_7")]
    assert await billing.sweep_trials(ends - timedelta(days=6, hours=20)) == []
    # T-3 and T-1
    assert await billing.sweep_trials(ends - timedelta(days=3)) == [("demo", "reminder_3")]
    assert await billing.sweep_trials(ends - timedelta(hours=20)) == [("demo", "reminder_1")]
    assert await billing.sweep_trials(ends - timedelta(hours=1)) == []
    sub = await _sub(app)
    assert sub.status == SubscriptionStatus.TRIALING
    assert set(sub.trial_notices) == {"reminder_7", "reminder_3", "reminder_1"}

    # expiry -> grace: past_due, calls still answered
    assert await billing.sweep_trials(ends + timedelta(minutes=1)) == [("demo", "expired")]
    sub = await _sub(app)
    assert sub.status == SubscriptionStatus.PAST_DUE
    assert sub.status_reason == TRIAL_GRACE_REASON and sub.unpaid_trial
    assert await billing.serving("demo")
    r = await client.get("/v1/billing/trial", params=DEMO)
    assert r.json()["state"] == "grace" and r.json()["calls_answered"] is True
    assert await billing.sweep_trials(ends + timedelta(days=2)) == []

    # grace over -> paused: suspended, calls stop
    assert await billing.sweep_trials(ends + timedelta(days=3, minutes=1)) == [("demo", "paused")]
    sub = await _sub(app)
    assert sub.status == SubscriptionStatus.SUSPENDED
    assert sub.status_reason == TRIAL_PAUSED_REASON and sub.unpaid_trial
    assert not await billing.serving("demo")
    r = await client.get("/v1/billing/trial", params=DEMO)
    assert r.json()["state"] == "paused" and r.json()["calls_answered"] is False
    assert await billing.sweep_trials(ends + timedelta(days=20)) == []

    # 30 days -> closed
    assert await billing.sweep_trials(ends + timedelta(days=30, minutes=1)) == [("demo", "closed")]
    sub = await _sub(app)
    assert sub.status == SubscriptionStatus.CANCELLED
    assert sub.status_reason == TRIAL_CLOSED_REASON
    r = await client.get("/v1/billing/trial", params=DEMO)
    assert r.json()["state"] == "closed"
    assert await billing.sweep_trials(ends + timedelta(days=60)) == []

    assert [e for _, e in seen] == [
        "reminder_7",
        "reminder_3",
        "reminder_1",
        "expired",
        "paused",
        "closed",
    ]


async def test_skipped_reminders_collapse_to_the_latest_one(
    client: AsyncClient, app: FastAPI
) -> None:
    billing: BillingService = app.state.billing
    ends = datetime(2026, 1, 15, 12, tzinfo=UTC)
    await _set_trial_end(app, ends)
    # sweep first runs at T-2: only the T-3 reminder goes out, T-7 is marked as sent
    assert await billing.sweep_trials(ends - timedelta(days=2)) == [("demo", "reminder_3")]
    sub = await _sub(app)
    assert set(sub.trial_notices) == {"reminder_7", "reminder_3"}
    # sweep next runs after expiry: expired only, no late reminders
    assert await billing.sweep_trials(ends + timedelta(hours=1)) == [("demo", "expired")]


async def test_paused_trial_declines_calls_with_spoken_notice(
    client: AsyncClient, app: FastAPI
) -> None:
    r = await client.get("/v1/numbers/search", params={**DEMO, "country": "GB", "limit": 1})
    e164 = r.json()[0]["e164"]
    r = await client.post("/v1/numbers", params=DEMO, json={"e164": e164, "assistant_id": "demo"})
    assert r.status_code == 201, r.text

    assert (await _admit(client, e164, "c1"))["allowed"] is True

    billing: BillingService = app.state.billing
    ends = datetime(2026, 1, 1, tzinfo=UTC)
    await _set_trial_end(app, ends)
    await billing.sweep_trials(ends + timedelta(days=1))  # grace
    assert (await _admit(client, e164, "c2"))["allowed"] is True
    await billing.sweep_trials(ends + timedelta(days=4))  # paused
    out = await _admit(client, e164, "c3")
    assert out["allowed"] is False and out["assistant_id"] == "demo"
    assert out["notice"] == UNAVAILABLE_NOTICE

    # closing returns the number to the platform pool
    await billing.sweep_trials(ends + timedelta(days=31))
    assert await billing.list_numbers("demo") == []
    pool = await billing.pool_numbers()
    assert any(n.e164 == e164 for n in pool)


async def test_paused_dashboard_is_read_only_except_billing(
    client: AsyncClient, app: FastAPI
) -> None:
    billing: BillingService = app.state.billing
    ends = datetime(2026, 1, 1, tzinfo=UTC)
    await _set_trial_end(app, ends)
    await billing.sweep_trials(ends + timedelta(days=1))
    # grace: still writable
    r = await client.post(
        "/v1/inbox/canned", params=DEMO, json={"title": "Grace", "body": "Thanks"}
    )
    assert r.status_code == 201, r.text

    await billing.sweep_trials(ends + timedelta(days=4))
    r = await client.post(
        "/v1/inbox/canned", params=DEMO, json={"title": "Paused", "body": "Thanks"}
    )
    assert r.status_code == 402
    assert "choose a plan" in r.json()["detail"]
    # reads still work
    r = await client.get("/v1/inbox/canned", params=DEMO)
    assert r.status_code == 200

    # subscribing (simulated Checkout) lifts the wall and resumes calls
    r = await client.post(
        "/v1/billing/checkout",
        params=DEMO,
        json={"plan_id": "starter", "return_url": "https://app.example/billing"},
    )
    assert r.status_code == 200, r.text
    sub = await _sub(app)
    assert sub.status == SubscriptionStatus.ACTIVE and sub.status_reason is None
    assert not sub.unpaid_trial and await billing.serving("demo")
    r = await client.post("/v1/inbox/canned", params=DEMO, json={"title": "Back", "body": "Thanks"})
    assert r.status_code == 201, r.text
    r = await client.get("/v1/billing/trial", params=DEMO)
    assert r.json()["state"] == "none"
    # and the sweep leaves paying tenants alone
    assert await billing.sweep_trials(ends + timedelta(days=40)) == []


async def test_subscribed_during_trial_never_enters_lifecycle(
    client: AsyncClient, app: FastAPI
) -> None:
    billing: BillingService = app.state.billing
    ends = datetime.now(UTC) + timedelta(days=10)
    await _set_trial_end(app, ends)
    r = await client.post(
        "/v1/billing/checkout",
        params=DEMO,
        json={"plan_id": "growth", "return_url": "https://app.example/billing"},
    )
    assert r.status_code == 200
    sub = await _sub(app)
    assert sub.subscription_ref and not sub.unpaid_trial
    assert await billing.sweep_trials(ends + timedelta(days=5)) == []
    assert await billing.sweep_trials(ends + timedelta(days=40)) == []
    assert (await _sub(app)).status == SubscriptionStatus.ACTIVE


async def test_staff_trial_extension_reopens_a_lapsed_trial(
    client: AsyncClient, app: FastAPI
) -> None:
    billing: BillingService = app.state.billing
    ends = datetime(2026, 1, 1, tzinfo=UTC)
    await _set_trial_end(app, ends)
    await billing.sweep_trials(ends + timedelta(days=1))
    await billing.sweep_trials(ends + timedelta(days=4))
    assert (await _sub(app)).status == SubscriptionStatus.SUSPENDED
    sub = await billing.extend_trial("demo", 7)
    assert sub.status == SubscriptionStatus.TRIALING and sub.trial_ends_at is not None
    assert sub.trial_ends_at > datetime.now(UTC)
    assert await billing.serving("demo")
