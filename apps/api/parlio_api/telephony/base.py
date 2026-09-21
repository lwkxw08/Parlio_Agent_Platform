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
    status: str = "active"  # active | pending (carrier regulatory review) | failed


class NumberRegion(BaseModel):
    """A UK area code users can pick when searching for a number."""

    code: str  # national destination code without the leading 0, e.g. "161"
    label: str
    kind: str = "geographic"  # geographic | national | freephone | mobile


UK_REGIONS: list[NumberRegion] = [
    NumberRegion(code="20", label="London (020)"),
    NumberRegion(code="161", label="Manchester (0161)"),
    NumberRegion(code="121", label="Birmingham (0121)"),
    NumberRegion(code="113", label="Leeds (0113)"),
    NumberRegion(code="114", label="Sheffield (0114)"),
    NumberRegion(code="115", label="Nottingham (0115)"),
    NumberRegion(code="116", label="Leicester (0116)"),
    NumberRegion(code="117", label="Bristol (0117)"),
    NumberRegion(code="118", label="Reading (0118)"),
    NumberRegion(code="131", label="Edinburgh (0131)"),
    NumberRegion(code="141", label="Glasgow (0141)"),
    NumberRegion(code="151", label="Liverpool (0151)"),
    NumberRegion(code="191", label="Newcastle / Tyneside (0191)"),
    NumberRegion(code="1223", label="Cambridge (01223)"),
    NumberRegion(code="1224", label="Aberdeen (01224)"),
    NumberRegion(code="1273", label="Brighton (01273)"),
    NumberRegion(code="1865", label="Oxford (01865)"),
    NumberRegion(code="1904", label="York (01904)"),
    NumberRegion(code="2380", label="Southampton (023 80)"),
    NumberRegion(code="2920", label="Cardiff (029 20)"),
    NumberRegion(code="28", label="Northern Ireland (028)"),
    NumberRegion(code="330", label="UK-wide 0330 (charged as a local call)", kind="national"),
    NumberRegion(code="800", label="Freephone 0800", kind="freephone"),
]


class TelephonyProvider(ABC):
    """Carrier-side operations Parlio needs: numbers, SIP trunk wiring, SMS, health.

    Call media itself never touches this layer - carriers deliver SIP to the LiveKit SIP edge.
    """

    name: str

    @abstractmethod
    async def search_numbers(
        self, country: str, limit: int = 5, area_code: str | None = None
    ) -> list[PhoneNumber]: ...

    @abstractmethod
    async def purchase_number(self, e164: str) -> PhoneNumber: ...

    @abstractmethod
    async def route_number_to_trunk(self, number: PhoneNumber, sip_uri: str) -> PhoneNumber: ...

    @abstractmethod
    async def release_number(self, number: PhoneNumber) -> None: ...

    async def number_status(self, number: PhoneNumber) -> str:
        """Carrier-side activation state; only carriers with post-order review override this."""
        return "active"

    @abstractmethod
    async def send_sms(self, from_e164: str, to_e164: str, body: str) -> str: ...

    @abstractmethod
    async def health(self) -> CarrierHealth: ...


class InboundEdge(ABC):
    """The platform's SIP edge: numbers the carrier delivers must be known to it so inbound
    INVITEs for a newly bought number are accepted and dispatched to the voice worker."""

    @abstractmethod
    async def add_number(self, e164: str) -> None: ...

    @abstractmethod
    async def remove_number(self, e164: str) -> None: ...
