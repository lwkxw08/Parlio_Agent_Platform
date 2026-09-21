"""Telnyx carrier adapter (REST API v2).

Env: PARLIO_TELNYX_API_KEY, PARLIO_TELNYX_CONNECTION_ID (the SIP connection pointing at the
LiveKit SIP edge), PARLIO_TELNYX_MESSAGING_PROFILE_ID. Nothing here is hard-coded to UK, but
default number searches target GB with the London media region.
"""

from __future__ import annotations

import asyncio

import httpx

from parlio_api.telephony.base import CarrierHealth, CarrierStatus, PhoneNumber, TelephonyProvider

API = "https://api.telnyx.com/v2"


class TelnyxProvider(TelephonyProvider):
    name = "telnyx"

    def __init__(
        self,
        api_key: str,
        connection_id: str | None = None,
        messaging_profile_id: str | None = None,
        client: httpx.AsyncClient | None = None,
        order_poll_s: float = 1.0,
    ) -> None:
        self._connection_id = connection_id
        self._messaging_profile_id = messaging_profile_id
        self._order_poll_s = order_poll_s
        self._client = client or httpx.AsyncClient(
            base_url=API, headers={"Authorization": f"Bearer {api_key}"}, timeout=15
        )

    async def search_numbers(
        self, country: str = "GB", limit: int = 5, area_code: str | None = None
    ) -> list[PhoneNumber]:
        params: dict[str, str | int | list[str]] = {
            "filter[country_code]": country,
            "filter[features]": ["voice"],  # UK geographic numbers are voice-only at Telnyx
            "filter[limit]": limit,
        }
        if area_code:
            params["filter[national_destination_code]"] = area_code
        r = await self._client.get("/available_phone_numbers", params=params)
        r.raise_for_status()
        return [
            PhoneNumber(provider=self.name, e164=n["phone_number"], country=country)
            for n in r.json()["data"]
        ]

    async def purchase_number(self, e164: str) -> PhoneNumber:
        body: dict[str, object] = {"phone_numbers": [{"phone_number": e164}]}
        if self._connection_id:
            body["connection_id"] = self._connection_id
        if self._messaging_profile_id:
            body["messaging_profile_id"] = self._messaging_profile_id
        r = await self._client.post("/number_orders", json=body)
        r.raise_for_status()
        # Orders complete asynchronously; the phone-number resource id (not the order id) is what
        # later PATCH/DELETE calls need. Fall back to the E.164, which Telnyx also accepts.
        ref = await self._phone_number_id(e164) or e164
        return PhoneNumber(
            provider=self.name,
            e164=e164,
            country=e164[:3],
            provider_ref=ref,
            sip_trunk_ref=self._connection_id,
        )

    async def _phone_number_id(self, e164: str, attempts: int = 10) -> str | None:
        for i in range(attempts):
            r = await self._client.get("/phone_numbers", params={"filter[phone_number]": e164})
            if r.status_code == 200 and (data := r.json().get("data")):
                return str(data[0]["id"])
            if i < attempts - 1:
                await asyncio.sleep(self._order_poll_s)
        return None

    async def route_number_to_trunk(self, number: PhoneNumber, sip_uri: str) -> PhoneNumber:
        # Telnyx routes by connection: the FQDN connection's target is the LiveKit SIP edge.
        if not self._connection_id:
            raise RuntimeError("PARLIO_TELNYX_CONNECTION_ID not configured")
        r = await self._client.patch(
            f"/phone_numbers/{number.provider_ref or number.e164}",
            json={"connection_id": self._connection_id},
        )
        if r.status_code != 404:  # order still pending: connection_id was set on the order itself
            r.raise_for_status()
        return number.model_copy(update={"sip_trunk_ref": self._connection_id})

    async def release_number(self, number: PhoneNumber) -> None:
        r = await self._client.delete(f"/phone_numbers/{number.provider_ref or number.e164}")
        r.raise_for_status()

    async def send_sms(self, from_e164: str, to_e164: str, body: str) -> str:
        payload: dict[str, object] = {"from": from_e164, "to": to_e164, "text": body}
        if self._messaging_profile_id:
            payload["messaging_profile_id"] = self._messaging_profile_id
        r = await self._client.post("/messages", json=payload)
        r.raise_for_status()
        return str(r.json()["data"]["id"])

    async def health(self) -> CarrierHealth:
        try:
            r = await self._client.get("/connections", params={"page[size]": 1})
            r.raise_for_status()
        except httpx.HTTPError as e:
            return CarrierHealth(provider=self.name, status=CarrierStatus.DOWN, detail=str(e))
        return CarrierHealth(provider=self.name, status=CarrierStatus.HEALTHY)
