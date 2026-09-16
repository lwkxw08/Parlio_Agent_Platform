"""Phase 21a: Insights read models (pure aggregation) + the dashboard route."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient

from parlio_api.insights import InsightInputs, build_insights
from parlio_api.store import (
    CallRecord,
    Member,
    TenantDoc,
    Ticket,
    TicketEvent,
    TicketStatus,
    TransferRecord,
)
from parlio_api.value import TrackingNumber, ValueSettings

NOW = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)  # Friday


def _call(
    cid: str,
    at: datetime,
    *,
    tenant: str = "demo",
    caller: str = "+447700900001",
    dialed: str = "+441610000001",
    answered: bool = True,
    duration: float = 90.0,
    text: str = "hello",
    transfers: list[dict[str, object]] | None = None,
    tickets: list[str] | None = None,
) -> CallRecord:
    return CallRecord(
        call_id=cid,
        tenant_id=tenant,
        company_id=f"{tenant}-main",
        assistant_id=f"{tenant}-a",
        caller=caller,
        dialed=dialed,
        status="completed",
        started_at=at,
        answered_at=at if answered else None,
        ended_at=at + timedelta(seconds=duration),
        duration_s=duration,
        transcript=[{"role": "user", "text": text}],
        extracted={"name": "Jo", "phone": caller} if answered else {},
        transfers=transfers or [],
        ticket_ids=tickets or [],
    )


def _inputs() -> InsightInputs:
    calls: list[CallRecord] = []
    # 4 weeks of Tuesday 09:00 + Thursday 14:00 calls (for the heat-map / forecast)
    for w in range(4):
        tue = NOW - timedelta(days=NOW.weekday() - 1 + 7 * w)
        thu = NOW - timedelta(days=NOW.weekday() - 3 + 7 * w)
        calls.append(_call(f"t{w}", tue.replace(hour=8), text="my boiler is broken, urgent"))
        calls.append(_call(f"t{w}b", tue.replace(hour=8, minute=1), caller="+447700900002"))
        calls.append(
            _call(f"h{w}", thu.replace(hour=13), caller="+447700900003", text="can I get a quote")
        )
    # after-hours answered call (would have been missed), a missed call, a transfer and a ticket
    calls.append(_call("ah", NOW - timedelta(days=2, hours=-9), caller="+447700900004"))  # 21:00
    calls.append(_call("miss", NOW - timedelta(days=1), answered=False, duration=0))
    calls.append(
        _call(
            "tr",
            NOW - timedelta(days=3),
            transfers=[{"transfer_id": "x1", "destination": "Dave", "department": "accounts"}],
        )
    )
    calls.append(_call("tk", NOW - timedelta(days=4), tickets=["tk-1"], caller="+447700900005"))
    # a different tenant's call must never leak in
    calls.append(_call("other", NOW - timedelta(days=1), tenant="acme"))
    # last year's same period for YoY
    calls.append(_call("ly", NOW - timedelta(days=370)))

    t_created = NOW - timedelta(days=4)
    tickets = [
        Ticket(
            id="tk-1",
            tenant_id="demo",
            company_id="demo-main",
            call_id="tk",
            status=TicketStatus.RESOLVED,
            department="accounts",
            reason="invoice",
            created_at=t_created,
            resolved_at=t_created + timedelta(hours=6),
            assigned_to="u-dave",
        ),
        Ticket(
            id="tk-2",
            tenant_id="demo",
            company_id="demo-main",
            status=TicketStatus.OPEN,
            department="sales",
            reason="quote",
            sla_breached=True,
            created_at=NOW - timedelta(hours=30),
        ),
    ]
    events = {
        "tk-1": [
            TicketEvent(ticket_id="tk-1", type="created", at=t_created),
            TicketEvent(
                ticket_id="tk-1", type="claimed", actor="u-dave", at=t_created + timedelta(hours=2)
            ),
            TicketEvent(
                ticket_id="tk-1", type="callback", actor="u-dave", at=t_created + timedelta(hours=3)
            ),
            TicketEvent(
                ticket_id="tk-1", type="resolved", actor="u-dave", at=t_created + timedelta(hours=6)
            ),
        ]
    }
    transfers = [
        TransferRecord(
            id="x1",
            tenant_id="demo",
            call_id="tr",
            destination="Dave",
            destination_id="u-dave",
            department="accounts",
            mode="warm",
            outcome="answered",
            started_at=NOW - timedelta(days=3),
            human_duration_s=180,
            recorded=True,
        ),
        TransferRecord(
            id="x2",
            tenant_id="demo",
            call_id="t0",
            destination="Sam",
            department="sales",
            mode="warm",
            outcome="no_answer",
            started_at=NOW - timedelta(days=2),
        ),
    ]
    booking = TenantDoc(
        kind="booking",
        id="bk-1",
        tenant_id="demo",
        data={
            "call_id": "h1",
            "status": "confirmed",
            "created_at": (NOW - timedelta(days=1, hours=20)).isoformat(),
        },
        created_at=NOW - timedelta(days=1),
    )
    qa = TenantDoc(
        kind="qa_score",
        id="q1",
        tenant_id="demo",
        data={"call_id": "t0", "overall": 4, "tone": 3, "resolution": 4, "flags": ["unresolved"]},
        created_at=NOW - timedelta(days=1),
    )
    gap = TenantDoc(
        kind="insight",
        id="in-1",
        tenant_id="demo",
        data={"question": "Do you do gas safety certificates?", "count": 3, "status": "open"},
    )
    outbound = TenantDoc(
        kind="outbound_call",
        id="ob-1",
        tenant_id="demo",
        data={
            "purpose": "ticket_callback",
            "status": "completed",
            "outcome": "resolved",
            "attempts": [{"n": 1}],
        },
        created_at=NOW - timedelta(days=1),
    )
    return InsightInputs(
        tenant_id="demo",
        calls=calls,
        tickets=tickets,
        ticket_events=events,
        transfers=transfers,
        members=[
            Member(
                tenant_id="demo", user_id="u-dave", email="dave@x.com", name="Dave", role="agent"
            )
        ],
        bookings=[booking],
        outbound=[outbound],
        qa_scores=[qa],
        faq_insights=[gap],
        tracking_numbers=[
            TrackingNumber(tenant_id="demo", e164="+441610000001", channel="Google Ads")
        ],
        value=ValueSettings(tenant_id="demo", avg_job_value_pence=10_000, lead_to_sale_rate=0.5),
    )


def test_demand_heatmap_forecast_and_would_have_missed() -> None:
    r = build_insights(_inputs(), days=30, now=NOW)
    d = r.demand
    assert sum(sum(row) for row in d.heatmap) == 16  # only demo-tenant calls in the window
    assert d.heatmap[1][9] == 8  # Tuesday 09:00 London (08:00 UTC) x 2 calls x 4 weeks
    assert d.heatmap[3][14] == 4
    assert len(d.forecast) == 7 and d.forecast[0].day == "2026-09-12"
    tue = next(f for f in d.forecast if f.weekday == "Tue")
    assert tue.expected_calls >= 2 and 9 in tue.busiest_hours
    assert d.forecast_total > 0
    # the 21:00 answered call + the overlapping second Tuesday call each week
    assert d.after_hours_calls == 1 and d.would_have_missed_total == 1 + 4
    assert d.peak_concurrency == 2 and d.overlapping_calls == 4
    assert d.busiest_hours[0].label == "09:00"


def test_resolution_funnel_sla_transfers_and_workforce() -> None:
    r = build_insights(_inputs(), days=30, now=NOW)
    f = {s.key: s.count for s in r.resolution.funnel}
    assert f["answered"] == 15 and f["transferred"] == 1 and f["ticketed"] == 1
    assert f["ai"] == 13 and f["transfer_answered"] == 1 and f["human"] == 1
    assert r.resolution.ai_callbacks == 1 and r.resolution.ai_callbacks_resolved == 1
    assert r.resolution.first_contact_resolution_rate == pytest.approx(14 / 15, abs=1e-3)

    s = r.sla
    assert s.tickets == 2
    assert s.median_time_to_claim_h == 2
    assert s.median_time_to_first_callback_h == 3
    assert s.median_time_to_resolve_h == 6 and s.breached == 1 and s.breach_rate == 0.5
    assert s.callback_first_attempt_rate == 1.0
    assert s.backlog[0].department == "Sales" and s.backlog[0].open == 1
    assert s.backlog[0].oldest_h == pytest.approx(30, abs=0.1)

    t = r.transfers
    assert t.attempts == 2 and t.answered == 1 and t.abandoned == 1 and t.recorded == 1
    assert t.avg_human_s == 180 and t.human_minutes == 3.0
    rates = {row.label: row.answer_rate for row in t.by_department}
    assert rates == {"Accounts": 1.0, "Sales": 0.0}
    assert t.est_transfer_cost_pence > 0

    w = r.workforce
    dave = next(m for m in w.members if m.label == "Dave")
    assert dave.claimed == 1 and dave.resolved == 1 and dave.callbacks == 1
    assert dave.transfers_answered == 1 and dave.avg_resolution_h == 6
    assert w.unassigned_open == 1


def test_intents_revenue_cx_attribution_cost_and_trends() -> None:
    r = build_insights(_inputs(), days=30, now=NOW)
    labels = {s.label for s in r.intents.top}
    assert "Emergency" in labels and "Quote" in labels
    assert r.intents.gaps[0].question.startswith("Do you do gas") and r.intents.gaps[0].count == 3
    assert r.intents.est_gap_value_pence == 3 * 10_000 * 0.5

    v = r.revenue
    assert v.currency == "GBP" and v.calls == 16 and v.leads == 15 and v.bookings == 1
    assert v.attributed_pence == 10_000 + 14 * 5_000
    assert v.missed_pence > 0
    assert v.median_lead_to_booking_h is not None and v.median_lead_to_booking_h > 0
    assert v.repeat_caller_share is not None and v.repeat_caller_share > 0
    assert v.by_source[0].label == "Google Ads"

    cx = r.cx
    assert cx.qa_scored == 1 and cx.avg_qa == 4.0 and cx.frustrated >= 1
    assert cx.repeat_within_7d >= 1
    assert cx.qa_by_intent[0].label == "Emergency"

    a = r.attribution
    assert a.channels[0].label == "Google Ads" and a.channels[0].count == 16
    assert a.untracked_calls == 0 and a.bookings_by_channel[0].count == 1

    c = r.cost
    assert c.ai_minutes == pytest.approx(22.5) and c.human_minutes == 3.0
    assert c.hours_saved == 1.5 and c.cost_per_resolved_pence is not None

    t = r.trends
    assert t.window_days == 30 and t.avg_by_weekday[1] > t.avg_by_weekday[6]
    assert t.monthly[-1].period == "2026-09" and t.monthly[-1].bookings == 1
    assert sum(p.calls for p in t.monthly[-2:]) == 16  # window spans late Aug + Sep
    assert t.monthly[0].period == "2025-09" and len(t.monthly) == 13  # gaps filled
    assert t.yearly[-1].period == "2026" and t.quarterly[-1].period == "2026-Q3"
    assert t.same_period_last_year is not None and t.same_period_last_year.calls == 1
    assert t.yoy_calls_pct == 1500.0
    assert t.busiest_months[0].label == "Sep 2026"


def test_tenant_isolation_and_empty_tenant() -> None:
    r = build_insights(_inputs(), days=30, now=NOW)
    assert all("acme" not in s.label for s in r.revenue.top_customers)
    empty = build_insights(InsightInputs(tenant_id="nobody"), days=30, now=NOW)
    assert empty.demand.forecast_total == 0 and empty.revenue.leads == 0
    assert empty.resolution.first_contact_resolution_rate is None
    assert len(empty.demand.forecast) == 7 and empty.trends.monthly == []


async def test_insights_route(client: AsyncClient) -> None:
    r = await client.get("/v1/analytics/insights", params={"tenant_id": "demo", "days": 30})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tenant_id"] == "demo" and len(body["demand"]["heatmap"]) == 7
    assert {"demand", "resolution", "sla", "transfers", "intents", "revenue", "cx"} <= body.keys()
    assert {"workforce", "attribution", "cost", "trends"} <= body.keys()
    assert (await client.get("/v1/analytics/insights", params={"days": 3})).status_code == 422
