from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum

from pydantic import BaseModel


class CarrierStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"
    UNKNOWN = "unknown"


class CarrierHealth(BaseModel):
    provider: str
    status: CarrierStatus
    detail: str | None = None


class PhoneNumber(BaseModel):
    provider: str
    e164: str
    country: str
    provider_ref: str | None = None
    sip_trunk_ref: str | None = None


class TelephonyProvider(ABC):
    """Carrier-side operations Parlio needs: numbers, SIP trunk wiring, SMS, health.

    Call media itself never touches this layer - carriers deliver SIP to the LiveKit SIP edge.
    """

    name: str

    @abstractmethod
    async def search_numbers(self, country: str, limit: int = 5) -> list[PhoneNumber]: ...

    @abstractmethod
    async def purchase_number(self, e164: str) -> PhoneNumber: ...

    @abstractmethod
    async def route_number_to_trunk(self, number: PhoneNumber, sip_uri: str) -> PhoneNumber: ...

    @abstractmethod
    async def release_number(self, number: PhoneNumber) -> None: ...

    @abstractmethod
    async def send_sms(self, from_e164: str, to_e164: str, body: str) -> str: ...

    @abstractmethod
    async def health(self) -> CarrierHealth: ...
