import httpx

from parlio_api.telephony import PROVIDERS, TelnyxProvider
from parlio_api.telephony.base import CarrierStatus


def _provider(handler) -> TelnyxProvider:  # type: ignore[no-untyped-def]
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.telnyx.com/v2"
    )
    return TelnyxProvider(
        api_key="test", connection_id="conn-1", messaging_profile_id="mp-1", client=client
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
        raise AssertionError(req.url)

    p = _provider(handler)
    nums = await p.search_numbers("GB")
    assert nums[0].e164 == "+441612000000"
    bought = await p.purchase_number(nums[0].e164)
    assert bought.provider_ref == "order-1"
    assert bought.sip_trunk_ref == "conn-1"
    assert seen[0].url.params["filter[country_code]"] == "GB"
    assert "filter[national_destination_code]" not in seen[0].url.params
    await p.search_numbers("GB", area_code="161")
    assert seen[-1].url.params["filter[national_destination_code]"] == "161"


async def test_simulated_numbers_follow_area_code() -> None:
    from parlio_api.billing import SimulatedNumbers

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
