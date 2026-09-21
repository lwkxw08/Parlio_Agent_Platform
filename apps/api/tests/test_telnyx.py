import json

import httpx

from parlio_api.billing import (
    BillingService,
    SimulatedBilling,
    SimulatedNumbers,
    TenantNumber,
)
from parlio_api.messaging import LogSmsProvider, MessageService
from parlio_api.store import MemoryStore, TenantDoc
from parlio_api.telephony import PROVIDERS, TelnyxProvider
from parlio_api.telephony.base import CarrierStatus, PhoneNumber


def _provider(handler) -> TelnyxProvider:  # type: ignore[no-untyped-def]
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.telnyx.com/v2"
    )
    return TelnyxProvider(
        api_key="test",
        connection_id="conn-1",
        messaging_profile_id="mp-1",
        client=client,
        order_poll_s=0,
    )


def test_telnyx_is_registered_primary() -> None:
    assert PROVIDERS["telnyx"] is TelnyxProvider


async def test_search_and_purchase() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.url.path.endswith("/available_phone_numbers"):
            return httpx.Response(200, json={"data": [{"phone_number": "+441612000000"}]})
        if req.url.path.endswith("/number_orders"):
            return httpx.Response(200, json={"data": {"id": "order-1"}})
        if req.url.path.endswith("/phone_numbers"):
            # first poll: order still pending; second: number provisioned
            polls = sum(1 for r in seen if r.url.path.endswith("/phone_numbers"))
            data = [] if polls == 1 else [{"id": "pn-1", "phone_number": "+441612000000"}]
            return httpx.Response(200, json={"data": data})
        if req.url.path.endswith("/phone_numbers/pn-1") and req.method == "PATCH":
            return httpx.Response(200, json={"data": {"id": "pn-1"}})
        raise AssertionError(req.url)

    p = _provider(handler)
    nums = await p.search_numbers("GB")
    assert nums[0].e164 == "+441612000000"
    bought = await p.purchase_number(nums[0].e164)
    assert bought.provider_ref == "pn-1"
    assert bought.sip_trunk_ref == "conn-1"
    routed = await p.route_number_to_trunk(bought, "sip:edge")
    assert routed.sip_trunk_ref == "conn-1"
    assert seen[-1].method == "PATCH" and seen[-1].url.path.endswith("/phone_numbers/pn-1")
    assert seen[0].url.params["filter[country_code]"] == "GB"
    assert "filter[national_destination_code]" not in seen[0].url.params
    await p.search_numbers("GB", area_code="161")
    assert seen[-1].url.params["filter[national_destination_code]"] == "161"


async def test_purchase_falls_back_to_e164_when_order_pending() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/number_orders"):
            return httpx.Response(200, json={"data": {"id": "order-1"}})
        if req.url.path.endswith("/phone_numbers"):
            return httpx.Response(200, json={"data": []})
        if "/phone_numbers/" in req.url.path and req.method == "PATCH":
            return httpx.Response(404, json={"errors": [{"code": "10005"}]})
        raise AssertionError(req.url)

    p = _provider(handler)
    bought = await p.purchase_number("+441612000001")
    assert bought.provider_ref == "+441612000001"
    # routing a not-yet-provisioned number is not an error: the order carried the connection
    assert (await p.route_number_to_trunk(bought, "sip:edge")).sip_trunk_ref == "conn-1"


async def test_simulated_numbers_follow_area_code() -> None:
    sim = SimulatedNumbers()
    london = await sim.search_numbers("GB", 3)
    manc = await sim.search_numbers("GB", 3, area_code="0161")
    free = await sim.search_numbers("GB", 2, area_code="800")
    assert all(n.e164.startswith("+4420") and len(n.e164) == 13 for n in london)
    assert all(n.e164.startswith("+44161") and len(n.e164) == 13 for n in manc)
    assert all(n.e164.startswith("+44800") and len(n.e164) == 13 for n in free)
    assert len({n.e164 for n in london + manc + free}) == 8


async def test_sms_and_health_down() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/messages"):
            return httpx.Response(200, json={"data": {"id": "msg-1"}})
        return httpx.Response(503)

    p = _provider(handler)
    assert await p.send_sms("+441612000000", "+447700900000", "hi") == "msg-1"
    assert (await p.health()).status == CarrierStatus.DOWN


async def test_requirement_group_and_review_status() -> None:
    orders: list[dict[str, list[dict[str, str]]]] = []
    state = {"status": "requirement-info-under-review"}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/number_orders"):
            orders.append(json.loads(req.content))
            return httpx.Response(200, json={"data": {"id": "order-1"}})
        if req.url.path.endswith("/phone_numbers") or req.url.path.endswith("/phone_numbers/pn-1"):
            pn = {"id": "pn-1", "phone_number": "+441612000002", **state}
            data = [pn] if req.url.path.endswith("/phone_numbers") else pn
            return httpx.Response(200, json={"data": data})
        raise AssertionError(req.url)

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.telnyx.com/v2"
    )
    p = TelnyxProvider(
        "test", connection_id="conn-1", client=client, order_poll_s=0, requirement_group_id="rg-1"
    )
    bought = await p.purchase_number("+441612000002")
    assert orders[0]["phone_numbers"][0] == {
        "phone_number": "+441612000002",
        "requirement_group_id": "rg-1",
    }
    assert bought.status == "pending"
    assert await p.number_status(bought) == "pending"
    state["status"] = "active"
    assert await p.number_status(bought) == "active"
    state["status"] = "requirement-info-declined"
    assert await p.number_status(bought) == "failed"


async def test_billing_activation_sweep_notifies() -> None:
    class Numbers(SimulatedNumbers):
        statuses = iter(["pending", "active"])

        async def number_status(self, number: PhoneNumber) -> str:
            return next(self.statuses)

    store = MemoryStore(None)
    sms = MessageService(store, LogSmsProvider(), None)
    svc = BillingService(store, sms, SimulatedBilling(), Numbers(), sip_uri="sip:x")
    seen: list[TenantNumber] = []

    async def on_change(n: TenantNumber) -> None:
        seen.append(n)

    svc.on_number_status = on_change
    await svc.store.put_doc(
        TenantDoc(
            kind=svc.NUMBER_KIND,
            id="n1",
            tenant_id="t1",
            data=TenantNumber(
                tenant_id="t1",
                company_id="c1",
                e164="+441612000003",
                provider="telnyx",
                assistant_id="a1",
                status="pending",
            ).model_dump(mode="json"),
        )
    )
    assert await svc.activate_pending_numbers() == []
    changed = await svc.activate_pending_numbers()
    assert [n.status for n in changed] == ["active"] and seen[0].activated_at is not None
    assert (await svc.list_numbers("t1"))[0].status == "active"
